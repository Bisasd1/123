"""MathWeaver 测试集:python tests/run_all.py(无 pytest 依赖)。

覆盖:Registry / 黄金图谱校验 / 病灶检出 / 追溯 / 补丁失效传播 /
MathScene 构建 / Flask API 端到端(demo 模式)。
"""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
from pathlib import Path

# Windows 控制台默认 GBK,中文/数学符号会触发 UnicodeEncodeError
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["MW_VAR_DIR"] = tempfile.mkdtemp(prefix="mw_test_var_")

from app.exposition import build_math_scene  # noqa: E402
from app.ir import GraphPatch, ProblemSpec, ProofEdge, ProofGraph, ProofNode, apply_patch  # noqa: E402
from app.pipeline import load_golden_graph, run_pipeline, synthesize_solution, trace_node  # noqa: E402
from app.pipeline.pipeline import _plan_to_graph  # noqa: E402
from app.registry import get_registry  # noqa: E402
from app.validators import validate_all  # noqa: E402

GOLDEN = json.loads((ROOT / "app/registry/data/golden/diagonal_operator_graph.json").read_text(encoding="utf-8"))
PYTH = json.loads((ROOT / "app/registry/data/golden/pythagorean_area_graph.json").read_text(encoding="utf-8"))
PASSED, FAILED = [], []


def check(name: str, cond: bool, detail: str = ""):
    (PASSED if cond else FAILED).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"  [{detail}]" if detail and not cond else ""))


def t_registry():
    print("[1] Registry")
    reg = get_registry()
    check("foundations 加载", len(reg.foundations) >= 19)
    check("对角 pattern 加载", "diagonal_operator_spectral_classification" in reg.patterns)
    check("勾股 pattern 加载", "pythagorean_area_proof" in reg.patterns)
    pid, score = reg.match_pattern("ℓ² 上乘法算子 M_a 的点谱与连续谱")
    check("对角关键词模式匹配", pid == "diagonal_operator_spectral_classification" and score > 0)
    pid2, score2 = reg.match_pattern("用面积法证明勾股定理,直角三角形 a b c")
    check("勾股关键词模式匹配", pid2 == "pythagorean_area_proof" and score2 > 0)
    check("非数学文本不误匹配", reg.match_pattern("今天天气怎么样")[0] is None)


def t_golden():
    print("[2] 黄金图谱")
    g = ProofGraph.from_dict(GOLDEN)
    r = validate_all(g)
    check("对角图谱三层校验全绿", r["ok"], json.dumps(r["errors"][:2], ensure_ascii=False))
    check("对角图谱无警告", not r["warnings"], json.dumps(r["warnings"][:2], ensure_ascii=False))
    concls = [n for n in g.nodes if n.node_type == "conclusion"]
    check("对角图谱含 5 个结论节点", len(concls) == 5, str(len(concls)))
    obs = [o for o in g.obligations if o.required_for_node == "cont_conclusion"]
    check("连续谱 3 obligations 已 discharge", len(obs) == 3 and all(o.status == "discharged" for o in obs))

    pg = ProofGraph.from_dict(PYTH)
    pr = validate_all(pg)
    check("勾股面积法图谱校验全绿", pr["ok"], json.dumps(pr["errors"][:2], ensure_ascii=False))
    check("勾股面积法含构造/引理/计算", {"construction", "lemma", "calculation", "conclusion"}.issubset({n.node_type for n in pg.nodes}))


def t_pathologies():
    print("[3] 病灶检出(数学 lint / 图结构)")
    g = ProofGraph.from_dict(copy.deepcopy(GOLDEN))
    g.nodes.append(ProofNode.from_dict({
        "id": "bad1", "context_id": "ctx_case_continuous", "node_type": "calculation",
        "title": "x", "statement_latex": "x",
        "statement_canonical": {"claim_type": "implication",
                                "assumptions": [{"type": "not_in", "left": "lambda", "right": "A"}],
                                "conclusion": {"type": "inf_positive"}}}))
    r = validate_all(g)
    check("λ∉A⇒inf>0 被抓", any(e["rule"] == "lint.closure_confusion" for e in r["errors"]))
    g2 = ProofGraph.from_dict(copy.deepcopy(GOLDEN))
    g2.edges.append(ProofEdge.from_dict({"source_id": "pt_eigen", "target_id": "cont_inj", "relation": "uses_lemma"}))
    r2 = validate_all(g2)
    check("跨分支依赖被抓", any(e["rule"] == "graph.cross_case_dependency" for e in r2["errors"]))
    g3 = ProofGraph.from_dict(copy.deepcopy(GOLDEN))
    g3.nodes[0].source_refs = ["DEF-invented"]
    r3 = validate_all(g3)
    check("发明 source_ref 被抓", any(e["rule"] == "graph.dangling_source_ref" for e in r3["errors"]))
    g4 = ProofGraph.from_dict(copy.deepcopy(GOLDEN))
    for o in g4.obligations:
        if o.id == "O_dense":
            o.status = "open"; o.discharged_by = None
    r4 = validate_all(g4)
    check("open obligation 被抓", any("continuous_spectrum_obligations" in e["rule"] for e in r4["errors"]))
    g5 = ProofGraph.from_dict(copy.deepcopy(GOLDEN))
    g5.edges.append(ProofEdge.from_dict({"source_id": "final_classification", "target_id": "def_Ma", "relation": "uses_lemma"}))
    r5 = validate_all(g5)
    check("依赖环被抓", any(e["rule"] == "graph.cycle" for e in r5["errors"]))


def t_trace_and_patch():
    print("[4] 追溯 + 补丁失效传播")
    g = ProofGraph.from_dict(GOLDEN)
    tr = trace_node(g, "cont_conclusion")
    anchor_ids = {a["id"] for a in tr["foundation_anchors"]}
    check("追溯达 DEF-continuous-spectrum", "DEF-continuous-spectrum" in anchor_ids)
    check("追溯达 LEM-c00-dense", "LEM-c00-dense-in-l2" in anchor_ids)
    check("追溯链含目标与定义层", tr["chain"][0]["node_id"] == "cont_conclusion" and len(tr["chain"]) >= 10)
    patch = GraphPatch.from_dict({"replace_nodes": [{**g.node("inst_b").to_dict(), "explanation": "修订"}], "change_summary": "test patch"})
    g2, invalidated = apply_patch(g, patch)
    check("补丁产生新版本", g2.version == 2 and g2.parent_version_id == g.id)
    check("下游失效传播到结论", "cont_conclusion" in invalidated and "final_classification" in invalidated)
    check("上游不受影响", "def_Ma" not in invalidated)


def t_mathscene():
    print("[5] MathScene 构建")
    g = ProofGraph.from_dict(GOLDEN)
    scene = build_math_scene(g)
    check("对角 MathScene 有对象表", len(scene.object_registry) >= len(g.symbols))
    check("对角 MathScene 有谱图", scene.diagrams and scene.diagrams[0].diagram_type == "spectrum_plane")
    check("StepCapsule 覆盖所有节点", len(scene.step_capsules) == len(g.nodes))
    check("对角图示含 VisualBinding", len(scene.diagrams[0].bindings) >= 3)

    pg = ProofGraph.from_dict(PYTH)
    ps = build_math_scene(pg)
    check("勾股 MathScene 有面积图", ps.diagrams and ps.diagrams[0].diagram_type == "pythagorean_area")
    obj_ids = {o.id for o in ps.object_registry}
    check("勾股对象表含中心正方形", ("Q" in obj_ids) or ("center_square" in obj_ids))
    cap = next((c for c in ps.step_capsules if c.node_id in ("lemma_center_square", "center_square_lemma")), None)
    check("中心正方形节点有局部展开", cap is not None and (len(cap.substeps) >= 1 or len(cap.guarantees) >= 1))


def t_pipeline_demo():
    print("[6] Pipeline 事件流(demo 模式)")
    evs = list(run_pipeline("求 ℓ² 上乘法算子 M_a=(a_n x_n) 的谱分类:点谱、连续谱、剩余谱、预解集"))
    types = [e["type"] for e in evs]
    check("事件流结构", types[:2] == ["tool_call", "tool_result"] and types[-1] == "pipeline_done")
    done = evs[-1]
    check("demo 回退对角黄金图谱", done["mode"] == "golden_demo" and len(done["graph"]["nodes"]) == 25)

    evs2 = list(run_pipeline("用面积法证明勾股定理,直角三角形两直角边为 a,b,斜边为 c"))
    done2 = evs2[-1]
    check("demo 回退勾股黄金图谱", done2["type"] == "pipeline_done" and done2["graph"]["pattern_id"] == "pythagorean_area_proof")

    evs3 = list(run_pipeline("帮我写一首关于秋天的诗"))
    check("非数学输入礼貌拒绝", evs3[-1]["type"] == "pipeline_error" and "模式" in evs3[-1]["message"])


_OPEN_PLAN = {
    "is_provable": True,
    "title": "√2 是无理数",
    "domain": "number_theory",
    "goal": {"natural": "证明 √2 不是有理数", "latex": "\\sqrt{2}\\notin\\mathbb{Q}"},
    "objects": [
        {"id": "p", "symbol": "p", "name": "分子", "kind": "integer",
         "role": "numerator", "definition": "既约分数的分子", "introduced_by": "assume_rational"},
        {"id": "q", "symbol": "q", "name": "分母", "kind": "integer",
         "role": "denominator", "definition": "既约分数的分母", "introduced_by": "assume_rational"},
    ],
    "foundations": [
        {"id": "DEF-rational", "kind": "definition", "name": "有理数定义",
         "statement_latex": "q\\in\\mathbb{Q}\\iff q=a/b,\\ \\gcd(a,b)=1", "explanation": "既约分数表示"},
        {"id": "LEM-even-square", "kind": "lemma", "name": "偶数平方引理",
         "statement_latex": "2\\mid n^2\\implies 2\\mid n", "explanation": "奇偶性"},
    ],
    "steps": [
        {"id": "assume_rational", "type": "setup", "title": "反证假设",
         "natural": "假设 √2=p/q 且既约", "latex": "\\sqrt2=p/q,\\ \\gcd(p,q)=1",
         "depends_on": [], "uses_foundations": ["DEF-rational"], "justification": "反证法起点"},
        {"id": "square", "type": "calculation", "title": "两边平方",
         "natural": "得到 2q^2=p^2", "latex": "2q^2=p^2",
         "depends_on": ["assume_rational"], "uses_foundations": [], "justification": "代数变形"},
        {"id": "p_even", "type": "lemma", "title": "p 为偶数",
         "natural": "p^2 偶 ⇒ p 偶", "latex": "2\\mid p",
         "depends_on": ["square"], "uses_foundations": ["LEM-even-square"], "justification": "偶数平方引理"},
        {"id": "q_even", "type": "lemma", "title": "q 为偶数",
         "natural": "代回得 q 也偶", "latex": "2\\mid q",
         "depends_on": ["p_even", "square"], "uses_foundations": ["LEM-even-square"], "justification": "再次用引理"},
        {"id": "contradiction", "type": "conclusion", "title": "矛盾",
         "natural": "p,q 同为偶与既约矛盾,故 √2 无理", "latex": "\\bot",
         "depends_on": ["q_even"], "uses_foundations": [], "justification": "与 gcd(p,q)=1 矛盾",
         "pitfalls": ["必须先假定既约"]},
    ],
}


def t_open_domain():
    print("[7] 开放域通用证明(确定性组装)")
    spec = ProblemSpec(id="problem_sqrt2", raw_input="证明 √2 是无理数")
    g = _plan_to_graph(spec, _OPEN_PLAN)
    r = validate_all(g)
    check("开放域图谱三层校验全绿", r["ok"], json.dumps(r["errors"][:3], ensure_ascii=False))
    check("标记为 general_proof", g.pattern_id == "general_proof")
    check("含目标与结论节点", any(n.node_type == "goal" for n in g.nodes) and any(n.node_type == "conclusion" for n in g.nodes))
    check("声明了本题基础(local_foundations)", len(g.local_foundations) == 2)
    check("无环 + 边非空", len(g.edges) >= 5 and validate_all(g)["ok"])
    # 本题声明的基础可被引用而不报"发明来源"
    used_refs = {ref for n in g.nodes for ref in n.source_refs}
    check("引用本题基础不算悬空", "DEF-rational" in used_refs and not any(
        e["rule"].startswith("graph.dangling") for e in r["errors"]))
    # 但真正发明一个未声明的来源仍会被抓(保证未被削弱)
    g.node("square").source_refs = ["DEF-totally-invented"]
    r2 = validate_all(g)
    check("发明未声明来源仍被抓", any(e["rule"] == "graph.dangling_source_ref" for e in r2["errors"]))

    # 据结构图(确定性)重建解答
    g2 = _plan_to_graph(spec, _OPEN_PLAN)
    sol = "".join(synthesize_solution(g2))   # demo 模式 → 确定性拼装
    check("解答据结构图重建且收尾", "完整解答" in sol and "∎" in sol and "2q^2=p^2" in sol)
    check("解答覆盖全部主干步骤", all(k in sol for k in ("反证假设", "矛盾")))

    # 开放域 MathScene 可构建(通用图示)
    scene = build_math_scene(g2)
    check("开放域 MathScene 胶囊覆盖全节点", len(scene.step_capsules) == len(g2.nodes))
    check("开放域有通用图示 + 对象表", bool(scene.diagrams) and len(scene.object_registry) >= 2)
    check("追溯解析本题基础名称", any(
        a.get("name") == "有理数定义" for a in trace_node(g2, "contradiction")["foundation_anchors"]))


def t_flask():
    print("[8] Flask API 端到端")
    from app.server import create_app
    c = create_app().test_client()
    conv = c.post("/api/conversations", json={}).get_json()
    resp = c.post(f"/api/conversations/{conv['id']}/messages", json={"content": "求 ℓ² 上乘法算子 M_a 的点谱、连续谱、剩余谱与预解集。"})
    events = [json.loads(line[5:]) for line in resp.get_data(as_text=True).splitlines() if line.startswith("data:")]
    gid = next((e["graph_id"] for e in events if e["type"] == "pipeline_done"), None)
    check("SSE 产出 graph_id", gid is not None)
    g = c.get(f"/api/graphs/{gid}").get_json()
    check("图谱查询 + 深度", len(g["depths"]) == 25 and g["validation"]["ok"])
    scene = c.get(f"/api/graphs/{gid}/explain").get_json()["scene"]
    check("explain API 返回 MathScene", scene["diagrams"][0]["diagram_type"] == "spectrum_plane" and len(scene["step_capsules"]) == 25)
    tr = c.get(f"/api/graphs/{gid}/trace/final_classification").get_json()
    check("追溯 API", len(tr["foundation_anchors"]) >= 6)
    dbg = c.post(f"/api/graphs/{gid}/nodes/cont_notsurj/debug", json={"question": "为什么不能直接除?"}).get_json()
    check("调试追问(demo 回退)", "每项非零" in dbg["answer"])
    removed = c.post(f"/api/graphs/{gid}/animate", json={"conclusion_id": "cont_conclusion"})
    check("动画 API 已禁用", removed.status_code == 410)
    check("首页可达", c.get("/").status_code == 200)
    msgs = c.get(f"/api/conversations/{conv['id']}/messages").get_json()
    check("历史落库(user+assistant)", len(msgs) >= 2 and any(m["meta"].get("graph_id") for m in msgs))


if __name__ == "__main__":
    for t in (t_registry, t_golden, t_pathologies, t_trace_and_patch, t_mathscene,
              t_pipeline_demo, t_open_domain, t_flask):
        t()
    print(f"\n{'='*46}\n通过 {len(PASSED)} / {len(PASSED)+len(FAILED)}" + (f"  失败: {FAILED}" if FAILED else "  全部通过 ✓"))
    sys.exit(1 if FAILED else 0)
