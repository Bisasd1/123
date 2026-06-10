"""ProofGraph:整个系统的核心中间表示。

contexts + nodes + edges + obligations + symbols,版本化,可打补丁。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .base import IRModel, ir_list, new_id


@dataclass
class Symbol(IRModel):
    id: str = ""
    latex: str = ""
    name: str = ""
    role: str = ""                      # e.g. "set_of_diagonal_entries"
    definition_node_id: str | None = None
    aliases: list = field(default_factory=list)


@dataclass
class ProofContext(IRModel):
    id: str = ""
    parent_context_id: str | None = None
    assumptions: list = field(default_factory=list)       # LaTeX 字符串列表
    introduced_symbols: list = field(default_factory=list)
    active_case: str | None = None                        # 分类讨论标签,如 "A"/"B"/"C"


@dataclass
class ProofNode(IRModel):
    id: str = ""
    context_id: str = "ctx_root"
    node_type: str = "calculation"        # NODE_TYPES
    title: str = ""

    statement_latex: str = ""             # 给人看
    statement_natural: str = ""           # 给学生的自然语言解释
    statement_canonical: dict = field(default_factory=dict)  # 给机器:封闭 claim_type 枚举

    symbols_used: list = field(default_factory=list)      # Symbol.id 列表
    local_assumptions: list = field(default_factory=list)

    source_refs: list = field(default_factory=list)       # Registry 条目 id(DEF-*/LEM-*)
    source_level: str = "calculation"                     # SOURCE_LEVELS
    foundation_anchor_ids: list = field(default_factory=list)  # 追溯终点(Registry id)

    validation_status: str = "unchecked"                  # VALIDATION_STATUSES
    explanation: str = ""
    pitfalls: list = field(default_factory=list)

    # lineage
    generated_by: str = "template"        # template / llm / patch / golden
    supersedes: str | None = None


@dataclass
class ProofEdge(IRModel):
    source_id: str = ""                   # 前提
    target_id: str = ""                   # 结论(target 依赖 source)
    relation: str = "uses_lemma"          # EDGE_RELATIONS
    justification: str = ""


@dataclass
class ProofObligation(IRModel):
    id: str = ""
    description: str = ""
    required_for_node: str = ""
    status: str = "open"                  # OBLIGATION_STATUSES
    discharged_by: str | None = None


@dataclass
class ProofGraph(IRModel):
    id: str = ""
    problem_id: str = ""
    pattern_id: str = ""
    version: int = 1
    parent_version_id: str | None = None
    change_summary: str = ""

    title: str = ""
    contexts: list = ir_list(ProofContext)
    nodes: list = ir_list(ProofNode)
    edges: list = ir_list(ProofEdge)
    obligations: list = ir_list(ProofObligation)
    symbols: list = ir_list(Symbol)
    # 开放域证明:LLM 为本题显式声明的定义/公理/定理锚点(不在课程 Registry 中)。
    # 注册模式生成的图保持为空 —— 因此"禁止发明来源"的保证对注册模式依然成立。
    local_foundations: list = field(default_factory=list)

    # ------------------------------------------------------------- 查询助手

    def node(self, node_id: str) -> ProofNode | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def context(self, ctx_id: str) -> ProofContext | None:
        return next((c for c in self.contexts if c.id == ctx_id), None)

    def in_edges(self, node_id: str) -> list[ProofEdge]:
        """指向 node_id 的边,即它的前提。"""
        return [e for e in self.edges if e.target_id == node_id]

    def out_edges(self, node_id: str) -> list[ProofEdge]:
        return [e for e in self.edges if e.source_id == node_id]

    def dependencies(self, node_id: str) -> list[str]:
        return [e.source_id for e in self.in_edges(node_id)]

    def dependents(self, node_id: str) -> list[str]:
        return [e.target_id for e in self.out_edges(node_id)]

    def downstream(self, node_ids: list[str]) -> set[str]:
        """node_ids 的所有(传递)下游节点,不含自身。"""
        seen: set[str] = set()
        frontier = list(node_ids)
        while frontier:
            cur = frontier.pop()
            for nxt in self.dependents(cur):
                if nxt not in seen:
                    seen.add(nxt)
                    frontier.append(nxt)
        return seen - set(node_ids)

    def topo_depths(self) -> dict[str, int]:
        """每个节点的最长前提链深度(用于前端分层布局与追溯展示)。"""
        depths: dict[str, int] = {}

        def depth_of(nid: str, stack: tuple = ()) -> int:
            if nid in depths:
                return depths[nid]
            if nid in stack:           # 有环时防御,环由 validator 报错
                return 0
            deps = self.dependencies(nid)
            d = 0 if not deps else 1 + max(depth_of(p, stack + (nid,)) for p in deps)
            depths[nid] = d
            return d

        for n in self.nodes:
            depth_of(n.id)
        return depths


# ------------------------------------------------------------------ GraphPatch

@dataclass
class GraphPatch(IRModel):
    """局部修复:不直接改图,生成新版本。"""
    add_nodes: list = ir_list(ProofNode)
    remove_node_ids: list = field(default_factory=list)
    replace_nodes: list = ir_list(ProofNode)      # 按 id 替换,自动记录 supersedes
    add_edges: list = ir_list(ProofEdge)
    remove_edges: list = field(default_factory=list)   # [{source_id, target_id}]
    change_summary: str = ""


def apply_patch(graph: ProofGraph, patch: GraphPatch) -> tuple[ProofGraph, list[str]]:
    """应用补丁 → (新版本图, 被标记 needs_review 的下游节点 id)。"""
    data = graph.to_dict()
    new = ProofGraph.from_dict(data)
    new.id = new_id("graph")
    new.parent_version_id = graph.id
    new.version = graph.version + 1
    new.change_summary = patch.change_summary or "graph patch"

    touched: list[str] = []

    removed = set(patch.remove_node_ids)
    if removed:
        touched += list(removed)
        new.nodes = [n for n in new.nodes if n.id not in removed]
        new.edges = [e for e in new.edges
                     if e.source_id not in removed and e.target_id not in removed]

    by_id = {n.id: i for i, n in enumerate(new.nodes)}
    for rep in patch.replace_nodes:
        if rep.id in by_id:
            old = new.nodes[by_id[rep.id]]
            rep.supersedes = old.id
            rep.generated_by = rep.generated_by or "patch"
            if rep.validation_status == "unchecked":
                rep.validation_status = "needs_review"
            new.nodes[by_id[rep.id]] = rep
            touched.append(rep.id)

    for n in patch.add_nodes:
        if not n.id:
            n.id = new_id("node")
        n.generated_by = n.generated_by or "patch"
        new.nodes.append(n)

    drop = {(d.get("source_id"), d.get("target_id")) for d in patch.remove_edges}
    if drop:
        new.edges = [e for e in new.edges if (e.source_id, e.target_id) not in drop]
    new.edges.extend(patch.add_edges)

    # 失效传播:被改动节点的所有下游标记 needs_review
    invalidated = sorted(new.downstream(touched)) if touched else []
    for nid in invalidated:
        node = new.node(nid)
        if node and node.validation_status != "invalid":
            node.validation_status = "needs_review"
    return new, invalidated
