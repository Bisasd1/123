"""三层校验:Schema → 图结构 → 数学 lint。

返回统一的 issue 列表:{level, rule, message, node_id?}。
level: error(图不可用) / warning(可用但需复核)。
"""
from __future__ import annotations

from app.ir import (CANONICAL_CLAIM_TYPES, EDGE_RELATIONS, NODE_TYPES,
                    SOURCE_LEVELS, VALIDATION_STATUSES, ProofGraph)
from app.registry import get_registry


def issue(level: str, rule: str, message: str, node_id: str | None = None) -> dict:
    return {"level": level, "rule": rule, "message": message, "node_id": node_id}


# ================================================================= Layer 1

def validate_schema(graph: ProofGraph) -> list[dict]:
    out: list[dict] = []
    for n in graph.nodes:
        if not n.id:
            out.append(issue("error", "schema.missing_id", "存在缺少 id 的节点"))
        if n.node_type not in NODE_TYPES:
            out.append(issue("error", "schema.node_type",
                             f"非法 node_type: {n.node_type}", n.id))
        if n.validation_status not in VALIDATION_STATUSES:
            out.append(issue("error", "schema.validation_status",
                             f"非法 validation_status: {n.validation_status}", n.id))
        if n.source_level not in SOURCE_LEVELS:
            out.append(issue("error", "schema.source_level",
                             f"非法 source_level: {n.source_level}", n.id))
        if not n.statement_latex and not n.statement_natural:
            out.append(issue("error", "schema.empty_statement",
                             "节点没有任何形式的 statement", n.id))
        sc = n.statement_canonical
        if sc and not isinstance(sc, dict):
            out.append(issue("error", "schema.canonical_type",
                             f"statement_canonical 必须是对象,得到 {type(sc).__name__}: {sc!r}", n.id))
            sc = {}
        ct = (sc or {}).get("claim_type")
        if ct and ct not in CANONICAL_CLAIM_TYPES:
            out.append(issue("error", "schema.claim_type",
                             f"非法 canonical claim_type: {ct}", n.id))
        if not ct:
            out.append(issue("warning", "schema.no_canonical",
                             "缺少 statement_canonical,数学 lint 将受限", n.id))
    for e in graph.edges:
        if e.relation not in EDGE_RELATIONS:
            out.append(issue("error", "schema.edge_relation",
                             f"非法边类型 {e.relation}: {e.source_id}->{e.target_id}"))
    return out


# ================================================================= Layer 2

def validate_graph(graph: ProofGraph) -> list[dict]:
    out: list[dict] = []
    reg = get_registry()
    ids = [n.id for n in graph.nodes]
    id_set = set(ids)
    if len(ids) != len(id_set):
        dup = sorted({i for i in ids if ids.count(i) > 1})
        out.append(issue("error", "graph.duplicate_id", f"重复节点 id: {dup}"))

    # 开放域图谱可在 graph.local_foundations 中显式声明自己的定义/公理锚点;
    # 这些 id 与 Registry id 一起构成"合法来源集合"。注册模式图的 local_foundations
    # 为空,因此对它们而言该集合就是 Registry —— 发明来源仍会被判错。
    local_ids = {f.get("id") for f in getattr(graph, "local_foundations", [])
                 if isinstance(f, dict) and f.get("id")}

    def _known_ref(ref: str) -> bool:
        return reg.has_ref(ref) or ref in local_ids

    ctx_ids = {c.id for c in graph.contexts}
    for n in graph.nodes:
        if n.context_id not in ctx_ids:
            out.append(issue("error", "graph.unknown_context",
                             f"节点引用不存在的 context: {n.context_id}", n.id))
        for ref in n.source_refs:
            if not _known_ref(ref):
                out.append(issue("error", "graph.dangling_source_ref",
                                 f"source_ref『{ref}』不在 Registry 或本题声明的基础集合中(疑似发明来源)", n.id))
        for ref in n.foundation_anchor_ids:
            if not _known_ref(ref):
                out.append(issue("error", "graph.dangling_anchor",
                                 f"foundation anchor『{ref}』不在 Registry 或本题声明的基础集合中", n.id))

    for e in graph.edges:
        for end in (e.source_id, e.target_id):
            if end not in id_set:
                out.append(issue("error", "graph.dangling_edge",
                                 f"边引用不存在的节点: {e.source_id}->{e.target_id}"))

    # 环检测(Kahn)
    indeg = {nid: 0 for nid in id_set}
    adj: dict[str, list[str]] = {nid: [] for nid in id_set}
    for e in graph.edges:
        if e.source_id in id_set and e.target_id in id_set:
            indeg[e.target_id] += 1
            adj[e.source_id].append(e.target_id)
    queue = [nid for nid, d in indeg.items() if d == 0]
    seen = 0
    while queue:
        cur = queue.pop()
        seen += 1
        for nxt in adj[cur]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    if seen != len(id_set):
        cyc = sorted(nid for nid, d in indeg.items() if d > 0)
        out.append(issue("error", "graph.cycle", f"依赖图存在环,涉及: {cyc[:8]}"))

    # 兄弟 case context 之间禁止依赖(假设作用域规则)
    case_of: dict[str, str] = {}
    for c in graph.contexts:
        if c.active_case:
            case_of[c.id] = c.active_case
    node_ctx = {n.id: n.context_id for n in graph.nodes}
    for e in graph.edges:
        sc, tc = node_ctx.get(e.source_id), node_ctx.get(e.target_id)
        if sc in case_of and tc in case_of and sc != tc:
            out.append(issue("error", "graph.cross_case_dependency",
                             f"跨分支依赖: {e.source_id}(情形{case_of[sc]}) -> "
                             f"{e.target_id}(情形{case_of[tc]}),分支假设不可混用"))

    # obligation 一致性
    for ob in graph.obligations:
        if ob.required_for_node not in id_set:
            out.append(issue("error", "graph.obligation_orphan",
                             f"obligation {ob.id} 指向不存在的节点 {ob.required_for_node}"))
        if ob.status == "discharged" and (not ob.discharged_by or ob.discharged_by not in id_set):
            out.append(issue("error", "graph.obligation_discharge",
                             f"obligation {ob.id} 标记已完成但 discharged_by 无效"))
    return out


# ================================================================= Layer 3

def lint_math(graph: ProofGraph) -> list[dict]:
    """模式级数学 lint。

    - 注册模式:规则集由 pattern.lint_rules 选择(缺省全开)。
    - 开放域 / 未注册模式:无法对任意数学做领域级 lint,只跑与领域无关的
      结构性规则(结论可追溯性),避免对非谱/非几何题误报。
    """
    reg = get_registry()
    pat = reg.pattern(graph.pattern_id)
    if pat is None:
        active = {"conclusion_traceability"}
    else:
        active = set(pat.get("lint_rules", _ALL_RULES.keys()))
    out: list[dict] = []
    for rule_id in active:
        fn = _ALL_RULES.get(rule_id)
        if fn:
            out.extend(fn(graph))
    return out


def _rule_closure_confusion(graph: ProofGraph) -> list[dict]:
    """λ∉A ⇒ inf|a_n−λ|>0 是经典错误(混淆 A 与 Ā)。"""
    out = []
    for n in graph.nodes:
        c = n.statement_canonical if isinstance(n.statement_canonical, dict) else {}
        if c.get("claim_type") != "implication":
            continue
        assumptions = c.get("assumptions") or []
        concl = c.get("conclusion") or {}
        assumes_not_in_A = any(
            a.get("type") == "not_in" and str(a.get("right", "")).strip() in ("A", "closure_free_A")
            for a in assumptions if isinstance(a, dict))
        concludes_inf_pos = isinstance(concl, dict) and concl.get("type") == "inf_positive"
        if assumes_not_in_A and concludes_inf_pos:
            out.append(issue("error", "lint.closure_confusion",
                             "由 λ∉A 推出 inf|a_n−λ|>0 不成立:λ∉A ≠ λ∉Ā"
                             "(反例 a_n=1/n, λ=0)。", n.id))
    return out


def _rule_division(graph: ProofGraph) -> list[dict]:
    """在未排除 a_n=λ 的上下文里做除法 → 警告。"""
    out = []
    excluded_ctx = set()
    for ctx in graph.contexts:
        joined = " ".join(ctx.assumptions)
        if "\\notin A" in joined or "notin A" in joined or "\\neq" in joined:
            excluded_ctx.add(ctx.id)
    for n in graph.nodes:
        text = n.statement_latex + " " + n.statement_natural
        has_division = ("\\frac" in text and ("a_n-\\lambda" in text or "b_n" in text)) \
                       or "/(a_n-" in text
        if not has_division:
            continue
        ctx = graph.context(n.context_id)
        guarded = (n.context_id in excluded_ctx
                   or any("\\neq 0" in a or "notin" in a or "\\notin" in a
                          for a in n.local_assumptions)
                   or "Z_b" in text or "Z_\\lambda" in text or "b_n\\neq 0" in text
                   or "n\\notin" in text)
        if not guarded and ctx is not None:
            out.append(issue("warning", "lint.division_by_possible_zero",
                             "此处出现对 a_n−λ(或 b_n)的除法,但当前上下文未排除其为零;"
                             "需要分离 Z_λ={n: a_n=λ}。", n.id))
    return out


def _rule_continuous_obligations(graph: ProofGraph) -> list[dict]:
    """引用连续谱定义的结论必须三个条件全部 discharged。"""
    out = []
    for n in graph.nodes:
        if "DEF-continuous-spectrum" not in n.source_refs:
            continue
        if n.node_type != "conclusion":
            continue
        obs = [o for o in graph.obligations if o.required_for_node == n.id]
        if len(obs) < 3:
            out.append(issue("error", "lint.continuous_spectrum_obligations",
                             f"连续谱结论需要 3 个 proof obligations(单射/稠密/不满),"
                             f"当前只有 {len(obs)} 个。", n.id))
            continue
        open_obs = [o.id for o in obs if o.status != "discharged"]
        if open_obs:
            out.append(issue("error", "lint.continuous_spectrum_obligations",
                             f"连续谱结论存在未完成的 obligations: {open_obs}", n.id))
        # 三条 discharge 边是否真的存在
        rels = {(e.source_id, e.relation) for e in graph.in_edges(n.id)}
        n_discharge = sum(1 for (_, r) in rels if r == "discharges_definition_condition")
        if n_discharge < 3:
            out.append(issue("warning", "lint.continuous_spectrum_obligations",
                             f"指向该结论的 discharges_definition_condition 边只有 {n_discharge} 条,"
                             "应为 3 条(对应三个定义条件)。", n.id))
    return out


def _rule_conclusion_traceability(graph: ProofGraph) -> list[dict]:
    """每个结论节点必须能(沿依赖)追溯到至少一个 foundation 锚点。"""
    out = []
    for n in graph.nodes:
        if n.node_type != "conclusion":
            continue
        reachable_anchor = False
        seen: set[str] = set()
        stack = [n.id]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            node = graph.node(cur)
            if node and (node.foundation_anchor_ids or node.source_refs):
                reachable_anchor = True
                break
            stack.extend(graph.dependencies(cur))
        if not reachable_anchor:
            out.append(issue("error", "lint.conclusion_traceability",
                             "结论节点无法追溯到任何 Registry 基础来源。", n.id))
    return out


def _rule_cross_case(graph: ProofGraph) -> list[dict]:
    # 结构版已在 validate_graph 实现;此处留规则位,供 pattern 引用时不报缺失
    return []


_ALL_RULES = {
    "closure_confusion": _rule_closure_confusion,
    "division_by_possible_zero": _rule_division,
    "continuous_spectrum_obligations": _rule_continuous_obligations,
    "conclusion_traceability": _rule_conclusion_traceability,
    "cross_case_dependency": _rule_cross_case,
}


# ================================================================= 总入口

def validate_all(graph: ProofGraph) -> dict:
    schema = validate_schema(graph)
    structural = [] if any(i["level"] == "error" for i in schema) else validate_graph(graph)
    blocked = any(i["level"] == "error" for i in schema + structural)
    math = [] if blocked else lint_math(graph)
    issues = schema + structural + math
    return {
        "ok": not any(i["level"] == "error" for i in issues),
        "errors": [i for i in issues if i["level"] == "error"],
        "warnings": [i for i in issues if i["level"] == "warning"],
    }
