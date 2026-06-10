"""Layer 1 and MathScene exposition IR.

Animation used to be the final layer of MathWeaver. The current product surface
is a semantic MathScene instead: a checked ProofGraph plus object registry,
step capsules, diagrams, concept trace, examples, and counterexamples.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .base import IRModel, ir_list


@dataclass
class MathObject(IRModel):
    name: str = ""
    latex: str = ""
    kind: str = ""


@dataclass
class Goal(IRModel):
    description: str = ""
    latex: str = ""


@dataclass
class ProblemSpec(IRModel):
    id: str = ""
    raw_input: str = ""
    domain: str = ""
    pattern_id: str = ""
    pattern_confidence: float = 0.0
    objects: list = ir_list(MathObject)
    goals: list = ir_list(Goal)
    assumptions: list = field(default_factory=list)
    notation_map: dict = field(default_factory=dict)


@dataclass
class BackgroundSpec(IRModel):
    motivation: str = ""
    prerequisites: list = field(default_factory=list)
    key_objects: list = field(default_factory=list)
    route: list = field(default_factory=list)
    intuition_models: list = field(default_factory=list)
    common_pitfalls: list = field(default_factory=list)


@dataclass
class ObjectEntry(IRModel):
    id: str = ""
    symbol: str = ""
    name: str = ""
    kind: str = ""
    role: str = ""
    definition: str = ""
    properties: list = field(default_factory=list)
    introduced_by: str | None = None
    aliases: list = field(default_factory=list)


@dataclass
class ObjectRegistry(IRModel):
    objects: list = ir_list(ObjectEntry)

    def __iter__(self):
        return iter(self.objects)

    def __len__(self) -> int:
        return len(self.objects)

    def __getitem__(self, index):
        return self.objects[index]


@dataclass
class StepCapsule(IRModel):
    id: str = ""
    node_id: str = ""
    kind: str = ""
    title: str = ""
    summary: str = ""
    inputs: list = field(default_factory=list)
    action_or_claim: str = ""
    guarantees: list = field(default_factory=list)
    justification: str = ""
    outputs: list = field(default_factory=list)
    used_by: list = field(default_factory=list)
    assumptions: list = field(default_factory=list)
    source_refs: list = field(default_factory=list)
    object_refs: list = field(default_factory=list)
    visual_refs: list = field(default_factory=list)
    pitfalls: list = field(default_factory=list)
    substeps: list = field(default_factory=list)


@dataclass
class VisualObject(IRModel):
    id: str = ""
    kind: str = ""
    label: str = ""
    math_object_id: str | None = None
    attrs: dict = field(default_factory=dict)


@dataclass
class VisualBinding(IRModel):
    id: str = ""
    node_id: str | None = None
    math_object_id: str | None = None
    visual_object_id: str | None = None
    formula_refs: list = field(default_factory=list)
    description: str = ""


@dataclass
class DiagramSpec(IRModel):
    id: str = ""
    graph_id: str = ""
    title: str = ""
    diagram_type: str = ""
    summary: str = ""
    description: str = ""
    renderer: str = "svg"
    formulas: list = field(default_factory=list)
    svg: str = ""
    objects: list = ir_list(VisualObject)
    bindings: list = ir_list(VisualBinding)
    notes: list = field(default_factory=list)


@dataclass
class SceneView(IRModel):
    id: str = ""
    view_type: str = ""
    title: str = ""
    renderer: str = "html"
    description: str = ""
    object_ids: list = field(default_factory=list)
    node_ids: list = field(default_factory=list)


@dataclass
class ExampleSpec(IRModel):
    id: str = ""
    title: str = ""
    description: str = ""
    latex: str = ""
    object_refs: list = field(default_factory=list)
    objects: dict = field(default_factory=dict)
    expected: dict = field(default_factory=dict)


@dataclass
class CounterexampleSpec(IRModel):
    id: str = ""
    title: str = ""
    description: str = ""
    misconception: str = ""
    correction: str = ""
    lesson: str = ""
    latex: str = ""
    node_refs: list = field(default_factory=list)


@dataclass
class ConceptTraceSpec(IRModel):
    anchors: list = field(default_factory=list)


@dataclass
class MathSceneSpec(IRModel):
    id: str = ""
    graph_id: str = ""
    title: str = ""
    goal: str = ""
    summary: str = ""
    background: BackgroundSpec = field(default_factory=BackgroundSpec)
    object_registry: ObjectRegistry = field(default_factory=ObjectRegistry)
    step_capsules: list = ir_list(StepCapsule)
    diagrams: list = ir_list(DiagramSpec)
    concept_trace: ConceptTraceSpec = field(default_factory=ConceptTraceSpec)
    examples: list = ir_list(ExampleSpec)
    counterexamples: list = ir_list(CounterexampleSpec)
    views: list = ir_list(SceneView)
    built_by: str = "deterministic"
    generated_by: str = "deterministic"

    def to_dict(self) -> dict:
        data = super().to_dict()
        data["objects"] = data.get("object_registry", {}).get("objects", [])
        return data
