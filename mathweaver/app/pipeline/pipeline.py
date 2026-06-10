"""Pipeline:Problem Text → ProblemSpec → ProofGraph → Checked。

设计要点:
- 每个阶段独立 LLM 调用、独立可重试;LLM 不可用时回退 demo 模式(黄金图谱)。
- LLM 只做"识别模式 + 填充模板槽位",骨架/上下文/符号来自 pattern 模板。
- run_pipeline 是生成器,产出工具调用风格的事件流(SSE 直接转发)。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.core.llm_client import LLMClient, LLMNotConfigured, extract_json
from app.ir import (EDGE_RELATIONS, GraphPatch, ProblemSpec, ProofContext,
                    ProofEdge, ProofGraph, ProofNode, Symbol, apply_patch, new_id)
from app.registry import get_registry
from app.validators import validate_all

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "registry" / "data" / "golden"
GOLDEN_BY_PATTERN = {
    "diagonal_operator_spectral_classification": GOLDEN_DIR / "diagonal_operator_graph.json",
    "pythagorean_area_proof": GOLDEN_DIR / "pythagorean_area_graph.json",
}


# ================================================================ Layer 1: parse

PARSE_SYSTEM = """你是数学问题解析器。给定一道数学题,完成:
1. 在给出的 pattern 列表中选择最匹配的 pattern_id(都不匹配则 null);
2. 抽取核心数学对象与目标;
3. 把用户记号对齐到模板符号(notation_map: 用户写法 -> 模板符号id)。
输出 JSON: {"pattern_id": str|null, "pattern_confidence": 0~1, "domain": str,
"objects": [{"name","latex","kind"}], "goals": [{"description","latex"}],
"assumptions": [str], "notation_map": {str: str}}"""


def parse_problem(raw_input: str) -> ProblemSpec:
    reg = get_registry()
    spec = ProblemSpec(id=new_id("problem"), raw_input=raw_input)
    kw_pattern, kw_score = reg.match_pattern(raw_input)

    client = LLMClient()
    if client.configured:
        try:
            pat_briefs = [{"id": p["id"], "name": p["name"],
                           "keywords": p["keywords"][:8],
                           "template_symbols": [s["id"] for s in p["template_symbols"]]}
                          for p in reg.patterns.values()]
            data = client.complete_json(
                PARSE_SYSTEM,
                f"可用 patterns:\n{json.dumps(pat_briefs, ensure_ascii=False)}\n\n题目:\n{raw_input}",
                heavy=False, max_tokens=2000)
            spec = ProblemSpec.from_dict({**data, "id": spec.id, "raw_input": raw_input})
            # 交叉验证:LLM 给出的 pattern 必须真实存在,否则退回关键词匹配
            if spec.pattern_id and not reg.pattern(spec.pattern_id):
                spec.pattern_id = kw_pattern or ""
        except (ValueError, RuntimeError, LLMNotConfigured):
            pass
    if not spec.pattern_id:
        spec.pattern_id = kw_pattern or ""
        spec.pattern_confidence = kw_score
        spec.domain = spec.domain or (reg.pattern(spec.pattern_id) or {}).get("domain", "")
    return spec


# ================================================================ Layer 2: generate

GENERATE_SYSTEM = """你是数学证明图谱构建器。基于给定的 pattern 骨架,为这道题生成 ProofGraph 的节点与边。

硬性规则:
1. contexts 原样使用骨架提供的(不得增删 id);每个节点必须有 context_id。
2. 分支节点只能放进对应 case context,且禁止跨兄弟分支依赖。
3. source_refs / foundation_anchor_ids 只允许使用提供的 Registry id 列表中的值,禁止发明。
4. 每个节点必须给 statement_latex、statement_natural,以及 statement_canonical。
   claim_type 可用: membership|implication|equality|set_equality|operator_property|instantiation|computation|construction|geometry_property|area_decomposition|condition_check|case_cover。
5. node_type 可用: goal|setup|object|construction|definition|lemma|calculation|condition_check|case|case_split|example|counterexample|theorem_invocation|conclusion|remark。
6. 边 relation 可用: constructs|verifies_condition|uses_definition|uses_lemma|computes|instantiates|specializes|case_of|discharges_definition_condition|proves_subgoal|concludes|equivalent_rewrite。
7. 若 pattern skeleton 中有 standard_obligations,需要生成并由相应节点 discharge。
8. 节点粒度 = 一条可被验证或引用的数学断言;主干优先,微小代数/画图步骤放进 explanation/pitfalls。

输出 JSON: {"title": str, "nodes": [...], "edges": [...], "obligations": [...]}
节点字段: id, context_id, node_type, title, statement_latex, statement_natural, statement_canonical, symbols_used, local_assumptions, source_refs, source_level, foundation_anchor_ids, explanation, pitfalls。"""

def _normalize_llm_nodes(nodes: list) -> list:
    """容忍 LLM 输出噪声:statement_canonical 写成字符串时包装成 {claim_type}。"""
    for n in nodes:
        if isinstance(n, dict) and isinstance(n.get("statement_canonical"), str):
            n["statement_canonical"] = {"claim_type": n["statement_canonical"]}
    return nodes


def load_golden_graph(pattern_id: str | None = None) -> ProofGraph:
    path = GOLDEN_BY_PATTERN.get(pattern_id or "", GOLDEN_BY_PATTERN["diagonal_operator_spectral_classification"])
    g = ProofGraph.from_dict(json.loads(path.read_text(encoding="utf-8")))
    g.id = new_id("graph")
    return g


def generate_graph(spec: ProblemSpec, max_attempts: int = 3) -> tuple[ProofGraph, dict, str]:
    """返回 (graph, validation_report, mode)。mode: llm | golden_demo。"""
    reg = get_registry()
    pat = reg.pattern(spec.pattern_id)
    client = LLMClient()

    if pat is None:
        # 没有命中注册模式:配置了 LLM 时走"开放域"通用证明生成;否则(demo)礼貌拒绝。
        if client.configured:
            return generate_graph_open(spec)
        supported = "、".join(p["name"] for p in reg.patterns.values())
        raise ValueError(
            "未能识别问题模式。当前为 demo 模式(未配置 LLM),内置支持: " + supported +
            "。配置 LLM 后可对任意证明题生成结构图,或在 Registry 中新增 pattern。")

    if not client.configured:
        graph = load_golden_graph(spec.pattern_id)
        graph.problem_id = spec.id
        graph.pattern_id = spec.pattern_id or graph.pattern_id
        graph.change_summary = "demo 模式:LLM 未配置,加载对应黄金参照图谱"
        return graph, validate_all(graph), "golden_demo"

    foundations = [{"id": it["id"], "name": it["name"],
                    "statement_latex": it["statement_latex"],
                    "allowed_use": it.get("allowed_use", "")}
                   for it in reg.foundations_for_pattern(spec.pattern_id)]
    skeleton = pat["skeleton"]
    user_payload = (
        f"题目:\n{spec.raw_input}\n\n"
        f"解析结果(对象/目标/记号对齐):\n"
        f"{json.dumps({'objects': [o.to_dict() if hasattr(o,'to_dict') else o for o in spec.objects], 'goals': [g.to_dict() if hasattr(g,'to_dict') else g for g in spec.goals], 'notation_map': spec.notation_map}, ensure_ascii=False)}\n\n"
        f"pattern 骨架(contexts 原样使用,slots 需覆盖):\n"
        f"{json.dumps(skeleton, ensure_ascii=False)}\n\n"
        f"模板符号表:\n{json.dumps(pat['template_symbols'], ensure_ascii=False)}\n\n"
        f"允许引用的 Registry 条目:\n{json.dumps(foundations, ensure_ascii=False)}\n\n"
        f"已知易错点(写进相关节点的 pitfalls):\n"
        f"{json.dumps(pat['common_pitfalls'], ensure_ascii=False)}")

    feedback = ""
    last_report: dict = {}
    for _ in range(max_attempts):
        data = client.complete_json(GENERATE_SYSTEM, user_payload + feedback,
                                    heavy=True, max_tokens=14000)
        graph = ProofGraph.from_dict({
            "id": new_id("graph"), "problem_id": spec.id,
            "pattern_id": spec.pattern_id, "version": 1,
            "title": data.get("title", spec.raw_input[:60]),
            "contexts": skeleton["contexts"],
            "symbols": pat["template_symbols"],
            "nodes": _normalize_llm_nodes(data.get("nodes", [])),
            "edges": data.get("edges", []),
            "obligations": data.get("obligations", []),
            "change_summary": "LLM 生成",
        })
        for n in graph.nodes:
            n.generated_by = "llm"
        last_report = validate_all(graph)
        if last_report["ok"]:
            for n in graph.nodes:
                if n.validation_status == "unchecked":
                    n.validation_status = "valid"
            return graph, last_report, "llm"
        feedback = ("\n\n上一次生成未通过校验,错误如下,请修复后重新输出完整 JSON:\n"
                    + json.dumps(last_report["errors"][:10], ensure_ascii=False))
    # 多次失败:返回最后一版(带错误标记),由用户在调试视图修复
    for n in graph.nodes:
        n.validation_status = "needs_review"
    return graph, last_report, "llm"


# ====================================================== Layer 2b: 开放域通用证明

OPEN_PLAN_SYSTEM = """你是数学证明的结构规划器。给定任意一道证明题/求解题,先在脑中完成证明,
再把它拆成一个"证明结构计划"(供下游确定性地组装成证明图谱)。

要求:
1. 只输出一个合法 JSON,不要 markdown 围栏,不要解释。
2. 把证明拆成有序的 steps;每个 step 是"一条可被引用或验证的数学断言",主干优先,
   琐碎代数并入该步的 natural/justification。
3. 显式声明本题用到的全部基础(定义/公理/已知定理/原理)放进 foundations,
   每个给一个稳定 id(形如 DEF-..., AX-..., THM-..., LEM-..., PRIN-...)。
4. 每个 step 的 uses_foundations 只能引用 foundations 里声明过的 id;
   depends_on 只能引用更靠前的 step id(保持有向无环)。
5. 至少有一个 type=conclusion 的收尾 step。

输出 JSON 结构:
{
 "is_provable": true,
 "title": "简短标题",
 "domain": "如 number_theory / real_analysis / geometry / algebra ...",
 "goal": {"natural": "要证明什么(自然语言)", "latex": "目标的 LaTeX"},
 "objects": [{"id","symbol(LaTeX)","name","kind","role","definition","introduced_by(step id, 可空)"}],
 "foundations": [{"id","kind","name","statement_latex","explanation"}],
 "steps": [
   {"id":"slug", "type":"setup|definition|construction|lemma|calculation|case|condition_check|conclusion",
    "title":"短标题", "natural":"这一步在做什么/得到什么(中文)", "latex":"核心断言的 LaTeX",
    "depends_on":["更靠前的 step id"], "uses_foundations":["foundation id"],
    "justification":"为什么这一步成立(引用依赖与基础)", "pitfalls":["可选的易错点"]}
 ]
}
若输入不是一个可证明/可求解的数学命题,则输出 {"is_provable": false, "reason": "原因"}。"""


_OPEN_NODE_TYPE = {
    "setup": "setup", "definition": "definition", "construction": "construction",
    "lemma": "lemma", "calculation": "calculation", "case": "case",
    "case_split": "case_split", "condition_check": "condition_check",
    "conclusion": "conclusion", "goal": "goal", "object": "object",
    "theorem_invocation": "theorem_invocation", "remark": "remark", "example": "example",
}
_OPEN_CLAIM_TYPE = {
    "goal": "proof_goal", "setup": "instantiation", "definition": "construction",
    "construction": "construction", "lemma": "implication", "calculation": "computation",
    "case": "case_cover", "case_split": "case_cover", "condition_check": "condition_check",
    "conclusion": "proof_goal",
}
_OPEN_SOURCE_LEVEL = {
    "goal": "conclusion", "setup": "definition", "definition": "definition",
    "construction": "definition", "lemma": "derived_lemma", "calculation": "calculation",
    "case": "calculation", "case_split": "calculation", "condition_check": "calculation",
    "conclusion": "conclusion",
}


def _slug(raw: str, used: set[str], fallback: str = "step") -> str:
    s = re.sub(r"[^0-9a-zA-Z_]+", "_", str(raw or "")).strip("_").lower()
    if not s:
        s = new_id(fallback)
    base, i = s, 2
    while s in used:
        s = f"{base}_{i}"
        i += 1
    used.add(s)
    return s


def _rel_for(target_type: str) -> str:
    rel = {"conclusion": "concludes", "calculation": "computes", "lemma": "uses_lemma",
           "construction": "constructs", "definition": "uses_definition",
           "case": "case_of", "condition_check": "verifies_condition"}.get(target_type, "uses_lemma")
    return rel if rel in EDGE_RELATIONS else "uses_lemma"


def _plan_to_graph(spec: ProblemSpec, plan: dict) -> ProofGraph:
    """把 LLM 的"证明结构计划"确定性地组装成一个合法 ProofGraph。

    由本函数掌控节点类型/边关系/枚举/无环性,因此产物默认就能通过结构校验 ——
    这是开放域稳定性的关键:不要求 LLM 直接吐出受严格枚举约束的 ProofGraph。
    """
    used_ids: set[str] = set()

    # ---- foundations(本题声明的基础)→ graph.local_foundations
    foundations = []
    found_ids: set[str] = set()
    for f in plan.get("foundations", []) or []:
        if not isinstance(f, dict):
            continue
        fid = str(f.get("id") or "").strip() or _slug(f.get("name") or "DEF", used_ids, "DEF")
        if fid in found_ids:                       # 同一 id 重复声明 → 去重
            fid = _slug(fid, used_ids, "DEF")
        found_ids.add(fid)
        used_ids.add(fid)                          # 预占,避免 step/object slug 撞车
        foundations.append({
            "id": fid, "kind": f.get("kind", "definition"),
            "name": f.get("name", fid), "statement_latex": f.get("statement_latex", ""),
            "explanation": f.get("explanation", ""),
        })

    # ---- objects → symbols(供对象表 / 通用图示)
    symbols, intro_map = [], {}
    for o in plan.get("objects", []) or []:
        if not isinstance(o, dict):
            continue
        oid = _slug(o.get("id") or o.get("symbol") or o.get("name") or "obj", used_ids, "obj")
        intro_map[oid] = o.get("introduced_by")
        symbols.append(Symbol(
            id=oid, latex=o.get("symbol", "") or o.get("latex", ""),
            name=o.get("name", oid), role=o.get("role", "") or o.get("kind", ""),
            definition_node_id=None, aliases=[],
        ))

    # ---- goal 节点
    goal = plan.get("goal") or {}
    goal_id = _slug("goal", used_ids, "goal")
    goal_anchor = [foundations[0]["id"]] if foundations else []
    nodes = [ProofNode(
        id=goal_id, context_id="ctx_root", node_type="goal", title="目标",
        statement_latex=goal.get("latex", ""), statement_natural=goal.get("natural", ""),
        statement_canonical={"claim_type": "proof_goal"},
        source_level="conclusion", foundation_anchor_ids=list(goal_anchor),
        validation_status="valid", generated_by="llm_open",
        explanation="本题要证明/求解的目标。",
    )]

    # ---- step 节点(记录原始 id → 最终 id,且 step 只能依赖更靠前的 step)
    step_specs = [s for s in (plan.get("steps") or []) if isinstance(s, dict)]
    id_map: dict[str, str] = {}
    order_index: dict[str, int] = {}
    step_nodes = []
    for idx, s in enumerate(step_specs):
        raw_id = s.get("id") or f"step_{idx+1}"
        nid = _slug(raw_id, used_ids, "step")
        id_map[str(raw_id)] = nid
        order_index[nid] = idx
        ntype = _OPEN_NODE_TYPE.get(s.get("type", ""), "calculation")
        refs = [fid for fid in (s.get("uses_foundations") or []) if fid in found_ids]
        node = ProofNode(
            id=nid, context_id="ctx_root", node_type=ntype,
            title=(s.get("title") or s.get("natural") or nid)[:80],
            statement_latex=s.get("latex", ""),
            statement_natural=s.get("natural", ""),
            statement_canonical={"claim_type": _OPEN_CLAIM_TYPE.get(s.get("type", ""), "computation")},
            symbols_used=[], local_assumptions=[],
            source_refs=list(refs), source_level=_OPEN_SOURCE_LEVEL.get(s.get("type", ""), "calculation"),
            foundation_anchor_ids=list(refs),
            validation_status="valid", generated_by="llm_open",
            explanation=s.get("justification", ""),
            pitfalls=[p for p in (s.get("pitfalls") or []) if isinstance(p, str)],
        )
        step_nodes.append(node)
        nodes.append(node)

    # symbols 的引入节点回填
    for sym in symbols:
        intro = intro_map.get(sym.id)
        if intro and str(intro) in id_map:
            sym.definition_node_id = id_map[str(intro)]

    # ---- 边:depends_on(只连更靠前的 step,保证无环)
    edges: list[ProofEdge] = []
    seen_edge: set[tuple] = set()

    def _add_edge(src: str, dst: str, relation: str, why: str = ""):
        key = (src, dst)
        if src and dst and src != dst and key not in seen_edge:
            seen_edge.add(key)
            edges.append(ProofEdge(source_id=src, target_id=dst, relation=relation, justification=why))

    for s in step_specs:
        nid = id_map[str(s.get("id") or "")] if str(s.get("id") or "") in id_map else None
        if nid is None:
            continue
        for dep in (s.get("depends_on") or []):
            dep_id = id_map.get(str(dep))
            if dep_id and order_index.get(dep_id, 1 << 30) < order_index.get(nid, -1):
                node = next((n for n in step_nodes if n.id == nid), None)
                _add_edge(dep_id, nid, _rel_for(node.node_type if node else ""))

    # goal 连接:目标 → 首个 step(specializes);末步(或 conclusion)→ 顺接
    if step_nodes:
        _add_edge(goal_id, step_nodes[0].id, "specializes", "目标决定证明的起点。")
        # 给每个没有任何前提的 step 兜底连到它的前一个 step,避免孤立子图
        for prev, cur in zip(step_nodes, step_nodes[1:]):
            if not any(e.target_id == cur.id for e in edges):
                _add_edge(prev.id, cur.id, _rel_for(cur.node_type), "顺接上一证明步骤。")

    # ---- 确保存在 conclusion,且能追溯到锚点
    concl_nodes = [n for n in step_nodes if n.node_type == "conclusion"]
    if not concl_nodes and step_nodes:
        step_nodes[-1].node_type = "conclusion"
        step_nodes[-1].statement_canonical = {"claim_type": "proof_goal"}
        step_nodes[-1].source_level = "conclusion"
        concl_nodes = [step_nodes[-1]]
    for c in concl_nodes:
        _add_edge(goal_id, c.id, "proves_subgoal", "结论回到原始目标。")
        if not c.foundation_anchor_ids and not c.source_refs and foundations:
            c.foundation_anchor_ids = [foundations[0]["id"]]

    graph = ProofGraph(
        id=new_id("graph"), problem_id=spec.id, pattern_id="general_proof", version=1,
        title=plan.get("title") or (spec.raw_input[:50] if spec.raw_input else "数学证明"),
        change_summary="开放域:LLM 规划 + 确定性组装",
        contexts=[ProofContext(id="ctx_root", assumptions=list(spec.assumptions or []))],
        nodes=nodes, edges=edges, symbols=symbols, obligations=[],
    )
    graph.local_foundations = foundations
    return graph


def _plan_from_llm_streaming(spec: ProblemSpec):
    """流式产出 planning 片段(供前端显示"思考进度"),最后 yield ('plan', data)。

    先尝试流式;若流式中断或返回的 JSON 不完整,退回 complete_json(自带 JSON 修复重试)。
    """
    client = LLMClient()
    user_p = f"题目:\n{spec.raw_input}\n\n请输出该题的证明结构计划 JSON。"
    sys_p = OPEN_PLAN_SYSTEM + "\n\n再次强调:只输出一个合法 JSON,不要 markdown 围栏,不要解释文字。"
    buf: list[str] = []
    data = None
    try:
        for piece in client.chat_stream(
                [{"role": "system", "content": sys_p}, {"role": "user", "content": user_p}],
                heavy=True, temperature=0.2, max_tokens=9000):
            if piece:
                buf.append(piece)
                yield ("thinking", piece)
        data = extract_json("".join(buf))
    except (ValueError, RuntimeError, LLMNotConfigured):
        data = None
    if data is None:                       # 流式失败 / JSON 截断 → 非流式重试
        data = client.complete_json(OPEN_PLAN_SYSTEM, user_p, heavy=True, max_tokens=9000)
    yield ("plan", data)


def iter_generate_graph_open(spec: ProblemSpec):
    """生成器:planning 阶段流式 yield ('thinking', piece),最后 yield ('result', (graph, report, mode))。"""
    data = None
    for kind, payload in _plan_from_llm_streaming(spec):
        if kind == "thinking":
            yield ("thinking", payload)
        elif kind == "plan":
            data = payload
    if isinstance(data, list):
        data = {"steps": data}
    if not isinstance(data, dict):
        raise ValueError("未能解析出证明结构计划,请重试或把题目叙述得更完整。")
    if data.get("is_provable") is False:
        raise ValueError("这看起来不是一个可证明/可求解的数学命题:" +
                         str(data.get("reason", "请换一道证明题或求解题。")))
    if not data.get("steps"):
        raise ValueError("未能从题目中规划出证明步骤,请把题目叙述得更完整。")

    graph = _plan_to_graph(spec, data)
    report = validate_all(graph)
    if not report["ok"]:
        # 由构造法生成的图理论上应当通过;万一不过,降级标记而非崩溃,结构仍可展示。
        for n in graph.nodes:
            if n.validation_status == "valid":
                n.validation_status = "needs_review"
        graph.change_summary += "(部分校验未通过,已标记复核)"
    yield ("result", (graph, report, "llm_open"))


def generate_graph_open(spec: ProblemSpec) -> tuple[ProofGraph, dict, str]:
    """开放域(非流式封装):任意证明题 → 证明结构计划 → 确定性 ProofGraph。"""
    result = None
    for kind, payload in iter_generate_graph_open(spec):
        if kind == "result":
            result = payload
    if result is None:
        raise ValueError("开放域生成失败:未产出结构图。")
    return result


# ====================================================== Layer 3b: 据结构图重建解答

SOLUTION_SYSTEM = """你是数学讲解者。下面给你一道题已经校验过的"证明结构图"(目标 + 有序节点 +
依赖关系 + 引用的定义/定理)。请严格依据这个结构图,写出一篇完整、流畅、严谨的中文解答。

硬性要求:
1. 完全沿着结构图的节点顺序展开,每个关键节点对应解答中的一步,不要新增结构图之外的跳步,
   也不要省略结构图中的关键步骤。
2. 公式用 $...$ 行内、$$...$$ 独立显示;保持记号与结构图一致。
3. 每一步说明依据(引用它依赖的步骤与基础定义/定理)。
4. 结构清晰:先点明目标与所用基础,再逐步推进,最后给出结论并以 ∎ 收尾。
5. 只输出解答正文(Markdown),不要复述本提示,不要输出 JSON。"""


def _ordered_nodes(graph: ProofGraph) -> list[ProofNode]:
    depths = graph.topo_depths()
    return sorted(graph.nodes, key=lambda n: (depths.get(n.id, 0),
                                              0 if n.node_type == "goal" else 1, n.id))


def _solution_digest(graph: ProofGraph) -> dict:
    reg = get_registry()
    found = {f.get("id"): f for f in getattr(graph, "local_foundations", []) if isinstance(f, dict)}
    foundations = []
    seen = set()
    for n in graph.nodes:
        for ref in list(n.foundation_anchor_ids) + list(n.source_refs):
            if ref in seen:
                continue
            seen.add(ref)
            item = reg.foundation(ref) or found.get(ref)
            if item:
                foundations.append({"id": ref, "name": item.get("name", ref),
                                    "statement_latex": item.get("statement_latex", "")})
    steps = []
    for n in _ordered_nodes(graph):
        steps.append({
            "id": n.id, "type": n.node_type, "title": n.title,
            "statement_natural": n.statement_natural, "statement_latex": n.statement_latex,
            "justification": n.explanation,
            "depends_on": [graph.node(d).title for d in graph.dependencies(n.id) if graph.node(d)],
            "uses": list(n.source_refs),
        })
    goal_node = next((n for n in graph.nodes if n.node_type == "goal"), None)
    return {
        "title": graph.title,
        "goal": (goal_node.statement_natural or goal_node.statement_latex) if goal_node else graph.title,
        "goal_latex": goal_node.statement_latex if goal_node else "",
        "foundations": foundations,
        "steps": steps,
    }


def _deterministic_solution(graph: ProofGraph):
    """无 LLM 时,据结构图确定性拼出可读解答(也是在线失败时的兜底)。"""
    d = _solution_digest(graph)
    yield f"## 完整解答:{d['title']}\n\n"
    if d["goal"]:
        yield f"**目标.** {d['goal']}"
        if d["goal_latex"]:
            yield f"  $${d['goal_latex']}$$"
        yield "\n\n"
    if d["foundations"]:
        yield "**所用定义 / 定理.**\n"
        for f in d["foundations"]:
            tail = f"：${f['statement_latex']}$" if f["statement_latex"] else ""
            yield f"- {f['name']}{tail}\n"
        yield "\n"
    yield "**证明.**\n\n"
    i = 0
    for s in d["steps"]:
        if s["type"] == "goal":
            continue
        i += 1
        head = f"**({i}) {s['title']}.** " if s["title"] else f"**({i})** "
        body = s["statement_natural"] or ""
        yield head + body
        if s["statement_latex"]:
            yield f"  $${s['statement_latex']}$$"
        if s["justification"]:
            yield f"\n*依据:* {s['justification']}"
        yield "\n\n"
    yield "因此目标得证。∎\n"


def synthesize_solution(graph: ProofGraph, spec: ProblemSpec | None = None):
    """生成器:据已校验的结构图,流式产出一篇完整流畅的解答(token 片段)。

    LLM 可用时让模型据结构图重建解答;不可用 / 失败时回退确定性拼装。始终有输出。
    """
    client = LLMClient()
    if not client.configured:
        yield from _deterministic_solution(graph)
        return
    digest = _solution_digest(graph)
    messages = [{"role": "system", "content": SOLUTION_SYSTEM},
                {"role": "user", "content": json.dumps(digest, ensure_ascii=False)}]
    produced = False
    try:
        for piece in client.chat_stream(messages, heavy=True, temperature=0.3, max_tokens=4000):
            if piece:
                produced = True
                yield piece
    except (RuntimeError, LLMNotConfigured):
        produced = False
    if not produced:
        yield from _deterministic_solution(graph)


# ================================================================ trace(纯图算法)

def trace_node(graph: ProofGraph, node_id: str) -> dict:
    """反向 BFS 到 foundation 锚点。不调用 LLM,毫秒级。"""
    reg = get_registry()
    start = graph.node(node_id)
    if start is None:
        raise ValueError(f"节点不存在: {node_id}")
    visited: dict[str, int] = {node_id: 0}
    order = [node_id]
    frontier = [node_id]
    while frontier:
        nxt: list[str] = []
        for nid in frontier:
            for e in graph.in_edges(nid):
                if e.source_id not in visited:
                    visited[e.source_id] = visited[nid] + 1
                    order.append(e.source_id)
                    nxt.append(e.source_id)
        frontier = nxt

    local = {f.get("id"): f for f in getattr(graph, "local_foundations", []) if isinstance(f, dict)}
    chain = []
    anchors: dict[str, dict] = {}
    for nid in order:
        node = graph.node(nid)
        if not node:
            continue
        entry = {"node_id": nid, "title": node.title, "level": visited[nid],
                 "node_type": node.node_type, "statement_latex": node.statement_latex,
                 "source_refs": node.source_refs,
                 "is_anchor": bool(node.foundation_anchor_ids or
                                   (node.source_refs and not graph.in_edges(nid)))}
        chain.append(entry)
        for ref in set(node.foundation_anchor_ids) | set(node.source_refs):
            item = reg.foundation(ref) or local.get(ref)
            if item:
                anchors[ref] = {"id": ref, "name": item.get("name", ref),
                                "kind": item.get("kind", "foundation"),
                                "statement_latex": item.get("statement_latex", ""),
                                "explanation": item.get("explanation", "")}
    edges = [{"source_id": e.source_id, "target_id": e.target_id,
              "relation": e.relation, "justification": e.justification}
             for e in graph.edges
             if e.source_id in visited and e.target_id in visited]
    return {"root": node_id, "chain": chain, "edges": edges,
            "foundation_anchors": list(anchors.values())}


# ================================================================ 调试追问 → GraphPatch

DEBUG_SYSTEM = """你是证明调试器。用户针对证明图中某个节点提出质疑/问题。
你要:1) 直接回答(中文,引用具体数学事实);2) 判断该节点是否确有错误。
若有错误,给出补丁。输出 JSON:
{"answer": str, "node_has_error": bool,
 "patch": null | {"replace_nodes": [完整节点对象], "add_nodes": [...], "add_edges": [...],
                  "remove_edges": [{"source_id","target_id"}], "change_summary": str}}
补丁中的节点字段与 ProofGraph 节点一致;source_refs 只能用提供的 Registry id。"""


def debug_node(graph: ProofGraph, node_id: str, question: str) -> dict:
    node = graph.node(node_id)
    if node is None:
        raise ValueError(f"节点不存在: {node_id}")
    reg = get_registry()
    client = LLMClient()

    if not client.configured:
        # demo 回退:用节点自带的 pitfalls / explanation 回答
        parts = []
        if node.pitfalls:
            parts.append("该节点标注的易错点:" + ";".join(node.pitfalls))
        if node.explanation:
            parts.append(node.explanation)
        ctx = graph.context(node.context_id)
        if ctx and ctx.assumptions:
            parts.append("当前上下文假设:" + ";  ".join(ctx.assumptions))
        deps = [graph.node(d).title for d in graph.dependencies(node_id) if graph.node(d)]
        if deps:
            parts.append("它依赖于:" + "、".join(deps))
        answer = ("(demo 模式,基于图谱元数据回答)\n" +
                  ("\n".join(parts) if parts else "该节点暂无附加说明。配置 LLM 后可获得针对性分析。"))
        return {"answer": answer, "node_has_error": False, "patch": None,
                "new_graph": None, "invalidated": []}

    ctx = graph.context(node.context_id)
    payload = {
        "question": question,
        "node": node.to_dict(),
        "context": ctx.to_dict() if ctx else None,
        "dependencies": [graph.node(d).to_dict() for d in graph.dependencies(node_id)
                         if graph.node(d)],
        "dependents_titles": [graph.node(d).title for d in graph.dependents(node_id)
                              if graph.node(d)],
        "registry_ids": list(reg.foundations.keys()),
    }
    data = client.complete_json(DEBUG_SYSTEM, json.dumps(payload, ensure_ascii=False),
                                heavy=True, max_tokens=5000)
    result = {"answer": data.get("answer", ""), "node_has_error": bool(data.get("node_has_error")),
              "patch": data.get("patch"), "new_graph": None, "invalidated": []}
    if data.get("patch"):
        patch = GraphPatch.from_dict(data["patch"])
        new_graph, invalidated = apply_patch(graph, patch)
        report = validate_all(new_graph)
        if report["ok"] or len(report["errors"]) == 0:
            result["new_graph"] = new_graph
            result["invalidated"] = invalidated
        else:
            result["answer"] += ("\n\n(生成的补丁未通过校验,已丢弃:"
                                 + json.dumps(report["errors"][:3], ensure_ascii=False) + ")")
            result["patch"] = None
    return result


# ================================================================ 阶段编排(事件流)

def run_pipeline(raw_input: str):
    """生成器:产出工具调用风格事件,供 SSE 直接转发。

    事件: {type: tool_call|tool_result|pipeline_done|pipeline_error, name, ...}
    """
    yield {"type": "tool_call", "name": "parse_problem", "label": "解析题目 / 识别问题模式"}
    try:
        spec = parse_problem(raw_input)
    except Exception as e:  # noqa: BLE001
        yield {"type": "pipeline_error", "stage": "parse", "message": str(e)}
        return
    yield {"type": "tool_result", "name": "parse_problem",
           "summary": f"pattern = {spec.pattern_id or '未识别'}",
           "data": spec.to_dict()}

    yield {"type": "tool_call", "name": "generate_graph", "label": "生成证明结构图(模型思考中)…"}
    try:
        reg = get_registry()
        pat = reg.pattern(spec.pattern_id)
        client = LLMClient()
        if pat is None and client.configured:
            # 开放域:流式 planning,把模型"思考进度"转发给前端
            graph = report = mode = None
            for kind, payload in iter_generate_graph_open(spec):
                if kind == "thinking":
                    yield {"type": "thinking", "stage": "plan", "content": payload}
                elif kind == "result":
                    graph, report, mode = payload
            if graph is None:
                raise ValueError("结构规划未产出结果。")
        else:
            graph, report, mode = generate_graph(spec)
    except Exception as e:  # noqa: BLE001
        yield {"type": "pipeline_error", "stage": "generate", "message": str(e)}
        return
    _mode_label = {"golden_demo": "demo 黄金图谱", "llm_open": "LLM 开放域生成",
                   "llm": "LLM 模板生成"}.get(mode, "LLM 生成")
    yield {"type": "tool_result", "name": "generate_graph",
           "summary": f"{len(graph.nodes)} 节点 / {len(graph.edges)} 边({_mode_label})"}

    yield {"type": "tool_call", "name": "validate_graph", "label": "三层校验(schema / 图结构 / 数学 lint)"}
    yield {"type": "tool_result", "name": "validate_graph",
           "summary": ("全部通过" if report.get("ok")
                       else f"{len(report.get('errors', []))} 错误 / {len(report.get('warnings', []))} 警告"),
           "data": report}

    yield {"type": "pipeline_done", "graph": graph.to_dict(),
           "problem_spec": spec.to_dict(), "validation": report, "mode": mode}
