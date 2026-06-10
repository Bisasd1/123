"""Layer 4:从 ProofGraph 的一条路径生成教学 Storyboard,再编译为 AnimationSpec。

逻辑顺序 ≠ 讲解顺序:这里负责教学节奏。
确定性构建器保证无 LLM 也能产出可渲染的故事板;LLM 模式只润色 narration。
"""
from __future__ import annotations

from app.ir import (AnimationBeat, AnimationSpec, ProofGraph, StoryboardBeat,
                    StoryboardSpec, new_id)
from app.registry import get_registry

MAX_FORMULAS_PER_BEAT = 4      # render lint:单镜公式上限
MAX_BEATS = 14


# ---------------------------------------------------------------- 路径选择

def select_path(graph: ProofGraph, conclusion_id: str) -> list[str]:
    """结论节点 + 其直接证据链(discharge/proves/concludes 的前提,再带上一层关键依赖)。"""
    order: list[str] = []
    seen: set[str] = set()

    def add(nid: str):
        if nid not in seen and graph.node(nid):
            seen.add(nid)
            order.append(nid)

    primary = [e for e in graph.in_edges(conclusion_id)
               if e.relation in ("discharges_definition_condition", "proves_subgoal", "concludes")]
    for e in primary:
        for e2 in graph.in_edges(e.source_id):
            if e2.relation in ("proves_subgoal", "computes"):
                add(e2.source_id)
        add(e.source_id)
    add(conclusion_id)
    return order


# ---------------------------------------------------------------- 确定性故事板

def build_storyboard(graph: ProofGraph, conclusion_id: str,
                     selected: list[str] | None = None) -> StoryboardSpec:
    concl = graph.node(conclusion_id)
    if concl is None:
        raise ValueError(f"结论节点不存在: {conclusion_id}")
    path = selected or select_path(graph, conclusion_id)
    reg = get_registry()

    beats: list[StoryboardBeat] = []

    def beat(**kw) -> None:
        kw.setdefault("id", f"beat_{len(beats)+1:02d}")
        beats.append(StoryboardBeat.from_dict(kw))

    ctx = graph.context(concl.context_id)
    ctx_assumps = ctx.assumptions if ctx else []

    # 1) 目标
    beat(beat_type="show_goal", node_ids=[concl.id],
         narration=f"目标:{concl.title}" + (f"(当前假设:{';  '.join(ctx_assumps)})" if ctx_assumps else ""),
         formulas=[concl.statement_latex],
         visual_action="标题入场,目标公式居中",
         duration_hint=3.0)

    # 2) 若结论引用了带条件的定义 → 展开定义为清单
    def_item = None
    for ref in concl.source_refs:
        item = reg.foundation(ref)
        if item and item.get("statement_canonical", {}).get("conditions"):
            def_item = item
            break
    condition_nodes = [e.source_id for e in graph.in_edges(concl.id)
                       if e.relation == "discharges_definition_condition"]
    if def_item:
        beat(beat_type="expand_definition", node_ids=[concl.id],
             narration=f"展开『{def_item['name']}』的定义:需要逐一验证下列条件。",
             formulas=[concl.statement_latex] +
                      _definition_condition_formulas(def_item)[:3],
             label=def_item["id"],
             visual_action="目标上移,定义条件以清单出现",
             duration_hint=4.0)

    # 3) 逐条核对条件(或路径中的子目标)
    check_targets = condition_nodes or [nid for nid in path if nid != concl.id]
    for i, nid in enumerate(check_targets[:6]):
        node = graph.node(nid)
        if not node:
            continue
        beat(beat_type="check_condition", node_ids=[nid],
             narration=node.statement_natural or node.title,
             formulas=[node.statement_latex],
             label=node.title,
             visual_action=f"条件 {i+1} 打勾,展示依据",
             duration_hint=3.5)

    # 4) 反例镜头:路径里有 pitfall 的节点 → 用模式 unit_test 做反例
    pit_node = next((graph.node(nid) for nid in path
                     if graph.node(nid) and graph.node(nid).pitfalls), None)
    pat = reg.pattern(graph.pattern_id) or {}
    tests = pat.get("unit_tests", [])
    if pit_node and tests:
        if graph.pattern_id == "diagonal_operator_spectral_classification":
            t = next((t for t in tests if "1/n" in t.get("a_n", "")), tests[0])
            formulas = [f"a_n = {_to_latex_frac(t.get('a_n', ''))}", "\\lambda = 0",
                        "a_n - \\lambda \\neq 0\\ \\forall n", "\\inf_n |a_n-\\lambda| = 0"]
        elif graph.pattern_id == "pythagorean_theorem":
            t = tests[0]
            formulas = [f"a = {t.get('a', '')}, b = {t.get('b', '')}", f"c = {t.get('c', '')}",
                        f"a^2+b^2 = {t.get('a', 0)**2 + t.get('b', 0)**2}", f"c^2 = {t.get('c', 0)**2}"]
        else:
            t = tests[0]
            formulas = [f"{k} = {v}" for k, v in t.items() if k not in ("name", "expected")][:4]

        beat(beat_type="counterexample", node_ids=[pit_node.id],
             narration=f"易错点:{pit_node.pitfalls[0]}",
             formulas=formulas,
             label=t.get("name", "反例"),
             visual_action="反例框,逐条出现",
             duration_hint=4.0)

    # 5) 收束
    beat(beat_type="conclude", node_ids=[concl.id],
         narration=concl.explanation or f"所有条件成立,得到:{concl.title}。",
         formulas=[concl.statement_latex],
         visual_action="结论加框",
         duration_hint=3.0)

    sb = StoryboardSpec(
        id=new_id("sb"), graph_id=graph.id, selected_node_ids=path,
        title=f"逻辑链动画:{concl.title}", beats=beats[:MAX_BEATS],
        built_by="deterministic")
    return sb


def _definition_condition_formulas(item: dict) -> list[str]:
    """把『… ⟺ 条件1, 条件2, 条件3』的定义拆成单条公式列表。"""
    latex = item.get("statement_latex", "")
    if "\\iff" not in latex:
        return [latex]
    rhs = latex.split("\\iff", 1)[1]
    parts = [p.strip().rstrip(",") for p in rhs.split(",\\ ")]
    parts = [p for p in (q.strip() for q in parts) if p]
    return parts[:3] if parts else [rhs.strip()]


def _to_latex_frac(expr: str) -> str:
    expr = expr.strip()
    if expr == "1/n":
        return "\\tfrac{1}{n}"
    if expr == "(-1)^n":
        return "(-1)^n"
    return expr


# ---------------------------------------------------------------- 编译为 AnimationSpec

def compile_storyboard(sb: StoryboardSpec, quality: str = "m") -> AnimationSpec:
    """Storyboard → AnimationSpec:消毒公式、限幅、补默认参数。失败即抛错(render lint)。"""
    beats: list[AnimationBeat] = []
    for b in sb.beats:
        formulas = [sanitize_latex(f) for f in b.formulas if f and f.strip()]
        formulas = formulas[:MAX_FORMULAS_PER_BEAT]
        if not formulas and b.beat_type != "case_split":
            raise ValueError(f"beat {b.id} 没有可渲染的公式")
        params = {
            "narration": (b.narration or "").strip()[:120],
            "label": (b.label or "").strip()[:60],
            "formulas": formulas,
        }
        if b.beat_type == "expand_definition":
            params["parent"] = formulas[0]
            params["children"] = formulas[1:]
        beats.append(AnimationBeat(beat_type=b.beat_type, params=params,
                                   duration=max(1.5, min(b.duration_hint, 8.0))))
    if not beats:
        raise ValueError("storyboard 为空,无法编译")
    return AnimationSpec(id=new_id("anim"), storyboard_id=sb.id, graph_id=sb.graph_id,
                         scene_name="ProofScene", title=sb.title[:80],
                         quality=quality if quality in ("l", "m", "h") else "m",
                         beats=beats)


_FORBIDDEN = ("\\input", "\\write", "\\immediate", "\\def", "\\catcode", "\\csname", "$$")


def sanitize_latex(s: str) -> str:
    """渲染前消毒:去掉危险 TeX 原语与多余包裹,平衡花括号检查。"""
    s = s.strip().strip("$").strip()
    for bad in _FORBIDDEN:
        s = s.replace(bad, "")
    if s.count("{") != s.count("}"):
        raise ValueError(f"花括号不平衡的公式: {s[:60]}")
    return s
