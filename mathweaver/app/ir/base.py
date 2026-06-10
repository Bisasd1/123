"""IR 基础设施:所有中间表示的序列化基类。

刻意不依赖 pydantic —— 字段校验由 validators/ 层负责,
这里只提供 dict <-> dataclass 的稳定转换。
"""
from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass, field, fields


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


@dataclass
class IRModel:
    """所有 IR 对象的基类:容忍多余字段的 from_dict / 稳定的 to_dict。"""

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict):
        if not isinstance(data, dict):
            raise TypeError(f"{cls.__name__}.from_dict 需要 dict,得到 {type(data).__name__}")
        known = {f.name for f in fields(cls)}
        kwargs = {}
        for f in fields(cls):
            if f.name not in data:
                continue
            value = data[f.name]
            sub = _nested_type(f)
            if sub is not None and isinstance(value, list):
                value = [sub.from_dict(v) if isinstance(v, dict) else v for v in value]
            kwargs[f.name] = value
        # 多余字段静默丢弃(LLM 输出常带噪声),缺失字段交给默认值
        unknown = set(data) - known
        obj = cls(**kwargs)
        if unknown:
            setattr(obj, "_unknown_fields", sorted(unknown))
        return obj


def _nested_type(f):
    """从 list[Sub] 注解里取出 Sub(若是 IRModel 子类)。"""
    t = f.metadata.get("item_type") if f.metadata else None
    return t


def ir_list(item_type):
    """声明嵌套 IRModel 列表字段。"""
    return field(default_factory=list, metadata={"item_type": item_type})


# ---------------------------------------------------------------- 枚举(封闭集)

NODE_TYPES = (
    "goal", "setup", "object", "construction", "definition", "lemma",
    "calculation", "condition_check", "case", "case_split", "example",
    "counterexample", "theorem_invocation", "conclusion", "remark",
)

EDGE_RELATIONS = (
    "constructs",           # 构造出对象/场景
    "verifies_condition",   # 验证适用条件 / 良定义性
    "uses_definition",      # 引用某个定义
    "uses_lemma",           # 调用引理
    "computes",             # 代数计算得到
    "instantiates",         # 通用引理代入特例
    "specializes",          # 一般结论的特殊化
    "case_of",              # 分类讨论的一支
    "discharges_definition_condition",
    "proves_subgoal",
    "concludes",
    "equivalent_rewrite",
)

VALIDATION_STATUSES = ("unchecked", "valid", "invalid", "needs_review")

OBLIGATION_STATUSES = ("open", "discharged", "failed", "needs_review")

SOURCE_LEVELS = (
    "course_foundation",
    "definition",
    "standard_lemma",
    "derived_lemma",
    "calculation",
    "conclusion",
)

CANONICAL_CLAIM_TYPES = (
    "membership",
    "implication",
    "equality",
    "set_equality",
    "operator_property",
    "instantiation",
    "computation",
    "construction",
    "geometry_property",
    "area_decomposition",
    "condition_check",
    "case_cover",
    "proof_goal",
)

SCENE_VIEW_TYPES = (
    "overview",
    "graph",
    "objects",
    "diagram",
    "node_detail",
    "trace",
    "examples",
    "counterexamples",
)
