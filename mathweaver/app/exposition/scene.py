"""MathScene / Exposition builder.

This module is the replacement for the old animation layer.  It converts a
checked ProofGraph into a semantic, multi-view explanation object: overview,
object registry, step capsules, static diagrams, visual bindings, examples and
concept trace.  It is deterministic and therefore safe to run in demo mode.
"""
from __future__ import annotations

from app.ir import (
    BackgroundSpec,
    ConceptTraceSpec,
    CounterexampleSpec,
    DetailLayer,
    DiagramSpec,
    ExampleSpec,
    MathSceneSpec,
    ObjectEntry,
    ObjectRegistry,
    ProofGraph,
    ProofNode,
    SceneView,
    StepCapsule,
    VisualBinding,
    VisualObject,
    new_id,
)
from app.registry import get_registry


def build_math_scene(graph: ProofGraph) -> MathSceneSpec:
    """Build a deterministic MathScene from a ProofGraph."""
    if graph.pattern_id == "pythagorean_area_proof":
        return _build_pythagorean_scene(graph)
    if graph.pattern_id == "diagonal_operator_spectral_classification":
        return _build_spectrum_scene(graph)
    return _build_generic_scene(graph)


# ---------------------------------------------------------------------------
# Shared helpers


def _node_title(graph: ProofGraph, node_id: str) -> str:
    n = graph.node(node_id)
    return n.title if n else node_id


def _role_to_kind(role: str) -> str:
    role = role or "object"
    if any(x in role for x in ("triangle", "square", "segment", "angle")):
        return "geometry"
    if any(x in role for x in ("operator", "spectrum", "resolvent")):
        return "operator"
    if any(x in role for x in ("space", "subspace")):
        return "space"
    if any(x in role for x in ("set", "closure", "sequence", "basis")):
        return "set/sequence"
    if any(x in role for x in ("parameter", "distance")):
        return "parameter"
    return role


def _object_registry_from_symbols(graph: ProofGraph, extras: list[ObjectEntry] | None = None) -> ObjectRegistry:
    entries: list[ObjectEntry] = []
    for s in graph.symbols:
        intro = s.definition_node_id
        intro_title = _node_title(graph, intro) if intro else "题目设定"
        refs = [n.id for n in graph.nodes if s.id in n.symbols_used]
        props = []
        if intro_title:
            props.append(f"由 {intro_title} 引入")
        if refs:
            props.append("关联节点:" + ", ".join(refs[:5]))
        entries.append(ObjectEntry(
            id=s.id,
            symbol=s.latex or s.id,
            name=s.name or s.id,
            kind=_role_to_kind(s.role),
            role=s.role,
            definition=f"{s.name or s.id}; 角色: {s.role or 'object'}",
            properties=props,
            introduced_by=intro,
            aliases=s.aliases,
        ))
    seen = {e.id for e in entries}
    for e in extras or []:
        if e.id not in seen:
            entries.append(e)
            seen.add(e.id)
    return ObjectRegistry(entries)


def _detail_layers_for_node(graph: ProofGraph, node: ProofNode) -> list[DetailLayer]:
    """从节点元数据确定性地构建渐进式细节层(空层不输出,UI 默认折叠)。

    层级语义:intuition(这一步在干什么)→ proof(为什么成立)→
    definition(引用了哪些底层定义/定理)→ referee(裁判式检查:前提/易错/义务)。
    """
    reg = get_registry()
    local = {f.get("id"): f for f in getattr(graph, "local_foundations", []) if isinstance(f, dict)}
    layers: list[DetailLayer] = []

    if node.statement_natural:
        layers.append(DetailLayer(level="intuition", title="直觉",
                                  blocks=[node.statement_natural]))

    proof_blocks = []
    if node.explanation:
        proof_blocks.append(node.explanation)
    proof_blocks.extend(f"依赖『{_node_title(graph, e.source_id)}』:{e.justification}"
                        for e in graph.in_edges(node.id) if e.justification)
    if proof_blocks:
        layers.append(DetailLayer(level="proof", title="证明依据", blocks=proof_blocks[:6]))

    def_blocks = []
    for ref in dict.fromkeys(list(node.source_refs) + list(node.foundation_anchor_ids)):
        item = reg.foundation(ref) or local.get(ref)
        if item:
            stmt = item.get("statement_latex", "")
            def_blocks.append(f"{item.get('name', ref)}({ref})"
                              + (f":${stmt}$" if stmt else ""))
    if def_blocks:
        layers.append(DetailLayer(level="definition", title="定义展开", blocks=def_blocks[:8]))

    referee_blocks = []
    ctx = graph.context(node.context_id)
    if ctx and ctx.assumptions:
        referee_blocks.append("生效前提:" + ";".join(f"${a}$" for a in ctx.assumptions[:4]))
    if node.local_assumptions:
        referee_blocks.append("局部假设:" + ";".join(node.local_assumptions[:4]))
    referee_blocks.extend(f"易错:{p}" for p in node.pitfalls)
    obs = [o for o in graph.obligations if o.required_for_node == node.id]
    referee_blocks.extend(f"义务[{o.status}]:{o.description}" for o in obs)
    if referee_blocks:
        layers.append(DetailLayer(level="referee", title="裁判检查", blocks=referee_blocks[:8]))
    return layers


def _capsule_for_node(graph: ProofGraph, node: ProofNode, extra_substeps: list[str] | None = None,
                      detail_layers: list[DetailLayer] | None = None,
                      contract: dict | None = None) -> StepCapsule:
    deps = graph.dependencies(node.id)
    outs = graph.dependents(node.id)
    incoming = graph.in_edges(node.id)
    outgoing = graph.out_edges(node.id)
    ctx = graph.context(node.context_id)

    inputs = [f"{dep}: {_node_title(graph, dep)}" for dep in deps]
    if not inputs and ctx and ctx.assumptions:
        inputs = list(ctx.assumptions)
    if not inputs and node.local_assumptions:
        inputs = list(node.local_assumptions)

    guarantees = []
    if node.statement_latex:
        guarantees.append(node.statement_latex)
    if node.statement_natural:
        guarantees.append(node.statement_natural)
    ctype = node.statement_canonical.get("claim_type") if isinstance(node.statement_canonical, dict) else ""
    if ctype:
        guarantees.append(f"canonical claim: {ctype}")

    justifications = []
    if node.explanation:
        justifications.append(node.explanation)
    justifications.extend(e.justification for e in incoming if e.justification)
    if node.source_refs:
        justifications.append("Registry 来源:" + ", ".join(node.source_refs))
    justification = "；".join(justifications) or "由该节点的依赖、上下文假设与对应定义/引理得到。"

    outputs = []
    if node.symbols_used:
        outputs.extend(node.symbols_used)
    if node.statement_latex:
        outputs.append(node.statement_latex)

    used_by = [f"{nid}: {_node_title(graph, nid)}" for nid in outs]
    if outgoing:
        used_by.extend(f"通过 {e.relation} → {_node_title(graph, e.target_id)}" for e in outgoing[:4])

    return StepCapsule(
        id=f"cap_{node.id}",
        node_id=node.id,
        kind=node.node_type,
        title=node.title,
        summary=node.statement_natural or node.title,
        inputs=inputs,
        action_or_claim=node.statement_latex or node.statement_natural or node.title,
        guarantees=guarantees[:6],
        justification=justification,
        outputs=outputs[:8],
        used_by=used_by[:8],
        assumptions=list(node.local_assumptions),
        source_refs=list(node.source_refs),
        object_refs=list(node.symbols_used),
        visual_refs=[f"v_{sid}" for sid in node.symbols_used],
        pitfalls=list(node.pitfalls),
        substeps=extra_substeps or _default_substeps(node),
        detail_layers=detail_layers if detail_layers is not None
        else _detail_layers_for_node(graph, node),
        construction_contract=contract or {},
    )


def _default_substeps(node: ProofNode) -> list[str]:
    subs = []
    if node.node_type in {"definition", "setup"}:
        subs.append("明确符号来源与对象类型。")
    if node.node_type == "construction":
        subs.append("记录输入对象、构造产物与需要验证的合法性条件。")
    if node.node_type == "lemma":
        subs.append("把局部命题作为后续可引用的中间资产。")
    if node.node_type == "calculation":
        subs.append("保留核心等式/估计,将机械代数放入节点细节。")
    if node.node_type == "conclusion":
        subs.append("检查输出是否回到原始目标。")
    return subs


def _concept_trace(graph: ProofGraph) -> ConceptTraceSpec:
    reg = get_registry()
    local = {f.get("id"): f for f in getattr(graph, "local_foundations", []) if isinstance(f, dict)}
    refs = []
    seen = set()
    for n in graph.nodes:
        for ref in list(n.foundation_anchor_ids) + list(n.source_refs):
            if ref in seen:
                continue
            seen.add(ref)
            item = reg.foundation(ref) or local.get(ref)
            if item:
                refs.append({
                    "id": ref,
                    "name": item.get("name", ref),
                    "kind": item.get("kind", "foundation"),
                    "statement_latex": item.get("statement_latex", ""),
                    "explanation": item.get("explanation", ""),
                    "node_refs": [n.id],
                })
            else:
                refs.append({"id": ref, "name": ref, "kind": "external", "statement_latex": "", "node_refs": [n.id]})
    return ConceptTraceSpec(anchors=refs)


def _standard_views(diagram_type: str) -> list[SceneView]:
    return [
        SceneView(id="view_overview", view_type="overview", title="总览", renderer="html"),
        SceneView(id="view_graph", view_type="graph", title="证明图谱", renderer="svg"),
        SceneView(id="view_objects", view_type="objects", title="对象表", renderer="html"),
        SceneView(id="view_diagram", view_type="diagram", title="静态图示", renderer="svg", description=diagram_type),
        SceneView(id="view_node_detail", view_type="node_detail", title="节点细节", renderer="html"),
        SceneView(id="view_trace", view_type="trace", title="定义追溯", renderer="html"),
    ]


# ---------------------------------------------------------------------------
# Pattern-specific scenes


def _build_pythagorean_scene(graph: ProofGraph) -> MathSceneSpec:
    extras = [
        ObjectEntry(
            id="four_triangles",
            symbol="T_1,T_2,T_3,T_4",
            name="四个全等直角三角形",
            kind="geometry",
            role="congruent_triangles",
            definition="与原直角三角形全等的四个副本。",
            properties=["总面积 4·ab/2", "与中心正方形共同填满外部正方形"],
            introduced_by="place_four_triangles",
        ),
        ObjectEntry(
            id="area_equation",
            symbol="(a+b)^2=4·ab/2+c^2",
            name="面积分解等式",
            kind="calculation",
            role="area_decomposition",
            definition="把同一个大正方形面积分解为四个三角形面积与中心正方形面积。",
            properties=["连接几何图示与代数等式"],
            introduced_by="calc_area_equation",
        ),
    ]
    registry = _object_registry_from_symbols(graph, extras)

    special = {
        "lemma_center_square": [
            "四条边分别是四个全等三角形的斜边,长度均为 c。",
            "每个中心角由原直角三角形的两个锐角拼成,两锐角互余,所以中心角为 90°。",
            "四边等长且四角为直角,因此中心区域 Q 是边长 c 的正方形。",
        ],
        "calc_area_equation": [
            "外部大正方形面积为 (a+b)^2。",
            "四个全等直角三角形总面积为 4·ab/2。",
            "中心正方形面积为 c^2。",
            "由面积可加性得到 (a+b)^2=4·ab/2+c^2。",
        ],
        "calc_simplify": [
            "展开 (a+b)^2=a^2+2ab+b^2。",
            "化简 4·ab/2=2ab。",
            "两边同时减去 2ab,得到 a^2+b^2=c^2。",
        ],
    }
    # 三个关键节点的"定义端 / 裁判层"深度展开(评审建议的 Definition / Referee 层)
    deep_layers = {
        "construct_outer_square": [
            DetailLayer(level="definition", title="定义展开", blocks=[
                "正方形(DEF-square):四边等长且四角为直角的简单四边形。",
                "边长取 $a+b$:是为了让每条边恰好能被分成长 $a$ 与长 $b$ 的两段,容纳一个三角形的两条直角边。",
            ]),
            DetailLayer(level="referee", title="裁判检查", blocks=[
                "前提:$a>0,\\ b>0$,否则分点退化。",
                "需要验证:四条边上的分点位置一致(都是距离顶点 $a$ 处),否则后续拼接不闭合。",
            ]),
        ],
        "place_four_triangles": [
            DetailLayer(level="definition", title="定义展开", blocks=[
                "全等(DEF-congruent-triangles):存在保持距离与角度的刚体运动把一个三角形映成另一个。",
                "每个 $T_i$ 由原三角形旋转 90° 依次放置,刚体运动保持边长 $a,b,c$ 与直角。",
            ]),
            DetailLayer(level="referee", title="裁判检查", blocks=[
                "需要验证:四个 $T_i$ 内部两两不交;它们与中心区域 $Q$ 恰好填满外部正方形 $S$。",
                "需要验证:$Q$ 的边界恰由四条斜边组成,无缝隙、无重叠。",
                "退化情形:$a=0$ 或 $b=0$ 时构造失效,已被前提排除。",
            ]),
        ],
        "lemma_center_square": [
            DetailLayer(level="definition", title="定义展开", blocks=[
                "正方形定义:简单四边形,四边相等且四角为直角。",
                "直角定义:角度为 $90^\\circ$。",
                "三角形内角和(LEM-triangle-angle-sum):$\\alpha+\\beta+\\gamma=180^\\circ$;直角三角形两锐角互余(LEM-acute-complement)。",
                "正方形面积(DEF-square-area):边长 $s$ 的正方形面积为 $s^2$。",
            ]),
            DetailLayer(level="referee", title="裁判检查", blocks=[
                "四条边分别来自四个全等三角形的斜边 ⇒ 四边长度均为 $c$。",
                "每个内角由相邻两个三角形的两个互余锐角拼成平角的剩余部分 ⇒ 均为 $90^\\circ$。",
                "需要验证:$Q$ 是简单四边形(无自交、非退化、连通)。",
                "不能只凭图形直觉断言 $Q$ 是正方形,必须给出上述两条验证。",
            ]),
        ],
    }
    contracts = {
        "place_four_triangles": {
            "node_id": "place_four_triangles",
            "operation": "在外部正方形内放置四个全等直角三角形",
            "inputs": ["原直角三角形 △ABC(直角边 a,b,斜边 c)", "外部正方形 S(边长 a+b)"],
            "outputs": ["T_1, T_2, T_3, T_4", "中心区域 Q"],
            "preconditions": ["a>0", "b>0", "△ABC 非退化"],
            "invariants": ["每个 T_i ≅ △ABC", "每个 T_i 的斜边长为 c", "T_i 内部两两不交"],
            "obligations": ["四个 T_i 与 Q 恰好填满 S", "Q 是简单四边形",
                            "Q 的四边长度均为 c", "Q 的四角均为 90°"],
            "degeneracy_warnings": ["a=0 或 b=0 时中心区域退化为整个 S 或一点"],
        },
        "construct_outer_square": {
            "node_id": "construct_outer_square",
            "operation": "构造边长为 a+b 的外部正方形",
            "inputs": ["线段长度 a, b"],
            "outputs": ["外部正方形 S", "四条边上的分点(距顶点 a 处)"],
            "preconditions": ["a>0", "b>0"],
            "invariants": ["S 的四边均为 a+b", "S 的四角均为直角"],
            "obligations": ["四个分点位置一致,使三角形可无缝放置"],
            "degeneracy_warnings": [],
        },
    }
    capsules = [
        _capsule_for_node(
            graph, n, special.get(n.id),
            detail_layers=_detail_layers_for_node(graph, n) + deep_layers.get(n.id, []),
            contract=contracts.get(n.id),
        )
        for n in graph.nodes
    ]
    diagram = DiagramSpec(
        id="diagram_pythagorean_area",
        graph_id=graph.id,
        title="勾股定理面积分解图",
        diagram_type="pythagorean_area",
        summary="外部大正方形、四个全等三角形与中心 c² 正方形之间的绑定。",
        description="公式项和几何区域一一绑定: (a+b)^2 ↔ 外部正方形, 4·ab/2 ↔ 四个三角形, c^2 ↔ 中心正方形;点击或悬停下方公式与图例可高亮对应区域。",
        renderer="svg",
        formulas=["(a+b)^2", "4\\cdot\\frac{ab}{2}", "c^2", "a^2+b^2=c^2"],
        objects=[
            VisualObject(id="v_outer_square", kind="region", label="外部正方形 S", math_object_id="outer_square", attrs={"formula": "(a+b)^2"}),
            VisualObject(id="v_four_triangles", kind="regions", label="四个全等三角形", math_object_id="four_triangles", attrs={"formula": "4ab/2"}),
            VisualObject(id="v_center_square", kind="region", label="中心正方形 Q", math_object_id="center_square", attrs={"formula": "c^2"}),
            VisualObject(id="v_edge_labels", kind="labels", label="边长 a,b,c", math_object_id="triangle_ABC", attrs={"labels": ["a", "b", "c"]}),
        ],
        bindings=[
            VisualBinding(id="bind_outer", node_id="construct_outer_square", math_object_id="outer_square", visual_object_id="v_outer_square", formula_refs=["(a+b)^2"], description="大正方形面积项"),
            VisualBinding(id="bind_triangles", node_id="place_four_triangles", math_object_id="four_triangles", visual_object_id="v_four_triangles", formula_refs=["4\\cdot\\frac{ab}{2}"], description="四个全等三角形总面积"),
            VisualBinding(id="bind_center", node_id="lemma_center_square", math_object_id="center_square", visual_object_id="v_center_square", formula_refs=["c^2"], description="中心正方形面积"),
            VisualBinding(id="bind_result", node_id="final_pythagorean", math_object_id="area_equation", visual_object_id="v_center_square", formula_refs=["a^2+b^2=c^2"], description="消去相同三角形面积后的结论"),
        ],
        notes=["图示是静态交互解释,不再依赖 Manim 动画。"],
    )
    background = BackgroundSpec(
        motivation="用同一个大正方形的两种面积表达,把几何构造转化为代数等式。",
        prerequisites=["直角三角形", "正方形面积", "三角形面积", "面积可加性", "代数化简"],
        key_objects=["△ABC", "外部大正方形 S", "四个全等三角形", "中心正方形 Q"],
        route=["设定直角三角形", "构造外部正方形", "放置四个全等三角形", "证明中心区域为 c²", "建立面积等式", "化简"],
        intuition_models=["同一总面积的两种分割", "公式项与区域高亮绑定", "消去相同区域得到剩余面积相等"],
        common_pitfalls=["不能只凭图形直觉断言中心区域是正方形。", "需要确认区域互不重叠且填满外部正方形。", "不要把 4·ab/2 错化简。"],
        construction_rationale=[
            {"question": "为什么这样构造?",
             "answer": "目标 a²+b²=c² 的三个平方项暗示把边长看成正方形面积;直接比较不方便,于是构造一个公共大面积。"},
            {"question": "为什么用边长 a+b 的大正方形?",
             "answer": "展开 (a+b)² 后出现 2ab,而四个直角三角形总面积恰为 4·ab/2=2ab,可以被消去。"},
            {"question": "证明路线是什么?",
             "answer": "构造 → 面积分解 → 代数消去。"},
            {"question": "哪里最容易错?",
             "answer": "中心区域是正方形不能只靠图看,要验证四边等长与四角直角。"},
        ],
    )
    return MathSceneSpec(
        id=new_id("scene"),
        graph_id=graph.id,
        title=graph.title or "勾股定理面积法完整图景",
        goal="证明 a^2+b^2=c^2",
        summary="主图保留证明骨架;对象表记录符号来源;图示绑定几何区域与公式项;节点细节说明每一步为何合法。",
        background=background,
        object_registry=registry,
        step_capsules=capsules,
        diagrams=[diagram],
        concept_trace=_concept_trace(graph),
        examples=[ExampleSpec(id="ex_3_4_5", title="3-4-5 直角三角形", description="a=3,b=4,c=5 时,9+16=25。", latex="3^2+4^2=5^2")],
        counterexamples=[CounterexampleSpec(id="ce_degenerate", title="退化边长", description="若 a=0 或 b=0,几何构造不再是非退化直角三角形。", misconception="任意非负 a,b 都可直接构造同样三角形", correction="需要 a>0,b>0。")],
        views=_standard_views("pythagorean_area"),
    )


def _build_spectrum_scene(graph: ProofGraph) -> MathSceneSpec:
    extras = [
        ObjectEntry(
            id="closure_A",
            symbol="\\overline{A}",
            name="对角元集合的闭包",
            kind="set/sequence",
            role="closure_of_diagonal_entries",
            definition="A={a_n} 在复平面中的闭包。",
            properties=["谱通常等于该闭包", "λ 到它的距离控制预解算子范数"],
            introduced_by="case_split_lambda",
        ),
        ObjectEntry(
            id="rho_Ma",
            symbol="\\rho(M_a)",
            name="预解集",
            kind="operator",
            role="resolvent_set",
            definition="使 M_a-λI 可逆且逆有界的 λ 的集合。",
            properties=["对应 dist(λ,A)>0"],
            introduced_by="res_conclusion",
        ),
    ]
    registry = _object_registry_from_symbols(graph, extras)
    special = {
        "case_split_lambda": [
            "按 λ 相对 A={a_n} 与 closure(A) 的位置分类。",
            "λ∈A 给出点谱;λ∈closure(A)\\A 给出连续谱候选;λ∉closure(A) 给出预解集。",
            "这三类覆盖复平面中的全部 λ。",
        ],
        "res_inverse_formula": [
            "逐坐标求逆得到除以 a_n-λ 的公式。",
            "正下界 m_λ>0 保证除法系数有界。",
        ],
        "cont_dense": [
            "有限支撑序列 c00 包含在值域中。",
            "c00 在 ℓ² 中稠密,所以值域稠密。",
        ],
    }
    capsules = [_capsule_for_node(graph, n, special.get(n.id)) for n in graph.nodes]
    diagram = DiagramSpec(
        id="diagram_spectrum_plane",
        graph_id=graph.id,
        title="复平面中的谱分类图",
        diagram_type="spectrum_plane",
        summary="把 A={a_n}、closure(A)、λ 与距离 m_λ 放在同一平面中。",
        description="观察 λ 相对 A 与 closure(A) 的位置即可理解点谱、连续谱和预解集的划分;点击或悬停下方公式与图例可高亮对应元素。",
        renderer="svg",
        formulas=["A=\\{a_n\\}", "m_\\lambda=\\inf_n |a_n-\\lambda|", "\\sigma(M_a)=\\overline{A}"],
        objects=[
            VisualObject(id="v_A_points", kind="point_cloud", label="A={a_n}", math_object_id="A", attrs={"formula": "A"}),
            VisualObject(id="v_closure_A", kind="region", label="closure(A)", math_object_id="closure_A", attrs={"formula": "\\overline{A}"}),
            VisualObject(id="v_lambda", kind="point", label="谱参数 λ", math_object_id="lambda", attrs={"formula": "λ"}),
            VisualObject(id="v_distance", kind="segment", label="m_λ 距离", math_object_id="m_lambda", attrs={"formula": "inf|a_n-λ|"}),
        ],
        bindings=[
            VisualBinding(id="bind_points", node_id="def_A", math_object_id="A", visual_object_id="v_A_points", formula_refs=["A=\\{a_n\\}"], description="对角元点集"),
            VisualBinding(id="bind_distance", node_id="def_mlambda", math_object_id="m_lambda", visual_object_id="v_distance", formula_refs=["m_\\lambda"], description="到点集/闭包的距离"),
            VisualBinding(id="bind_res", node_id="res_conclusion", math_object_id="rho_Ma", visual_object_id="v_lambda", formula_refs=["m_\\lambda>0"], description="距离正对应预解集"),
            VisualBinding(id="bind_cont", node_id="cont_conclusion", math_object_id="closure_A", visual_object_id="v_closure_A", formula_refs=["\\lambda\\in\\overline A\\setminus A", "\\sigma(M_a)=\\overline{A}"], description="闭包边界点给连续谱"),
        ],
        notes=["结构图代替线性视频,更适合谱分类的全局理解。"],
    )
    background = BackgroundSpec(
        motivation="把抽象算子谱问题转化为复平面中 λ 与点集 A={a_n} 的位置关系。",
        prerequisites=["ℓ²", "乘法/对角算子", "谱与预解集", "点谱/连续谱/剩余谱", "集合闭包", "稠密子空间"],
        key_objects=["M_a", "A={a_n}", "closure(A)", "λ", "m_λ", "预解算子"],
        route=["定义 M_a", "计算 M_a-λI", "按 λ 的位置分情况", "预解集:距离正", "点谱:命中坐标", "连续谱:闭包极限但未命中", "汇总谱分类"],
        intuition_models=["复平面点集模型", "距离 m_λ 控制逆算子范数", "c00 稠密解释连续谱值域稠密"],
        common_pitfalls=["不能把 λ∉A 误认为 dist(λ,A)>0。", "逐项可除不等于逆算子有界。", "连续谱需要单射、值域稠密且非满射/逆无界。"],
        construction_rationale=[
            {"question": "为什么按 λ 的位置分类?",
             "answer": "M_a−λI 仍是乘法算子,其可逆性完全由系数 a_n−λ 的下确界决定,于是谱性质化为 λ 与点集 A 的几何位置关系。"},
            {"question": "为什么引入距离 m_λ?",
             "answer": "m_λ=inf|a_n−λ| 正是逆算子范数的倒数:m_λ>0 ⟺ 逆有界 ⟺ λ∈ρ(M_a)。"},
            {"question": "哪里最容易错?",
             "answer": "λ∉A 只保证逐项非零,不保证 m_λ>0;闭包边界点正是连续谱的来源。"},
        ],
    )
    return MathSceneSpec(
        id=new_id("scene"),
        graph_id=graph.id,
        title=graph.title or "乘法算子谱分类完整图景",
        goal="刻画 σ(M_a)、ρ(M_a)、点谱、连续谱与剩余谱。",
        summary="用复平面结构图解释谱分类:λ 命中 A 是点谱;λ 在 closure(A)\\A 是连续谱;λ 离 closure(A) 有正距离是预解集。",
        background=background,
        object_registry=registry,
        step_capsules=capsules,
        diagrams=[diagram],
        concept_trace=_concept_trace(graph),
        examples=[ExampleSpec(id="ex_seq_1n", title="聚到 0 的对角序列", description="若 a_n=1/n,则 0 不在 A 中但属于 closure(A),对应连续谱位置。", latex="a_n=1/n")],
        counterexamples=[CounterexampleSpec(id="ce_notin_A", title="λ 不在 A 仍可能在谱中", description="λ∉A 只说明逐项非零,不保证距离正。", misconception="λ∉A ⇒ λ∈ρ(M_a)", correction="还要有 inf_n |a_n-λ|>0。")],
        views=_standard_views("spectrum_plane"),
    )


def _build_generic_scene(graph: ProofGraph) -> MathSceneSpec:
    registry = _object_registry_from_symbols(graph)
    capsules = [_capsule_for_node(graph, n) for n in graph.nodes]
    vobjects = [VisualObject(id=f"v_{o.id}", kind=o.kind, label=o.name or o.symbol, math_object_id=o.id) for o in registry.objects[:8]]
    diagram = DiagramSpec(
        id="diagram_generic",
        graph_id=graph.id,
        title="结构对象图",
        diagram_type="generic_structure",
        summary="由对象表自动生成的结构概览。",
        description="该题暂无专用图示模板,先展示核心对象及其关联。",
        renderer="svg",
        objects=vobjects,
        bindings=[VisualBinding(id=f"bind_{o.id}", math_object_id=o.id, visual_object_id=f"v_{o.id}") for o in registry.objects[:8]],
    )
    background = BackgroundSpec(
        motivation="把证明图谱拆成目标、设定、对象、构造、引理、计算与结论。",
        prerequisites=sorted({ref for n in graph.nodes for ref in n.source_refs})[:12],
        key_objects=[o.name or o.symbol for o in registry.objects[:8]],
        route=[n.title for n in graph.nodes[:10]],
        intuition_models=["证明骨架 + 节点细节 + 对象表"],
        common_pitfalls=[p for n in graph.nodes for p in n.pitfalls][:6],
    )
    return MathSceneSpec(
        id=new_id("scene"),
        graph_id=graph.id,
        title=graph.title or "数学完整图景",
        goal=next((n.statement_latex for n in graph.nodes if n.node_type in {"goal", "conclusion"}), ""),
        summary="当前使用通用 MathScene:主图显示推理骨架,对象表和节点细节补全理解链条。",
        background=background,
        object_registry=registry,
        step_capsules=capsules,
        diagrams=[diagram],
        concept_trace=_concept_trace(graph),
        examples=[],
        counterexamples=[],
        views=_standard_views("generic_structure"),
    )
