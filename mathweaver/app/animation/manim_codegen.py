"""确定性 Manim 代码生成:AnimationSpec → 自包含 .py 文件。

约束:
- LLM 永远不写 Manim 代码;本模块是唯一产出 Scene 的地方。
- 生成文件自包含(beat 模板内联),目标 ManimCE >= 0.19(随附 manim-main 为 0.20.1)。
- 公式经 sanitize 后以 Python 字面量嵌入(repr 保证反斜杠安全)。
"""
from __future__ import annotations

from app.ir import AnimationSpec

PRELUDE = '''\
"""{title}

由 MathWeaver 确定性 codegen 生成(AnimationSpec id: {spec_id})。
渲染:  manim render -q{quality} {{filename}} {scene_name}
依赖:  manim community >= 0.19(含 LaTeX);中文字幕需要系统含 CJK 字体。
"""
from manim import (BLACK, DOWN, LEFT, RIGHT, UP, Create, FadeIn, FadeOut,
                   MathTex, Rectangle, ReplacementTransform, Scene, SurroundingRectangle,
                   Text, VGroup, Write, config)

INK = "#16243D"
ACCENT = "#2B4C7E"
OK = "#2F7D5D"
AMBER = "#B7791F"
PAPER = "#F7F8FA"

config.background_color = PAPER


def T(s, size=24, color=INK, weight="NORMAL"):
    """中文/混排字幕用 Text(Pango),公式一律用 MathTex。"""
    return Text(s, font_size=size, color=color, weight=weight)


def F(s, color=INK, scale=1.0):
    """公式优先用 MathTex;含中文等 LaTeX 不兼容内容时降级为 Text。"""
    try:
        mob = MathTex(s, color=color)
    except Exception:
        mob = Text(s, font_size=26, color=color)
    return mob.scale(scale)


class {scene_name}(Scene):
    def setup(self):
        self.caption_mobj = None
        self.checklist = VGroup()
        self.checked = 0

    # ---------------------------------------------------------- 通用部件

    def caption(self, text):
        new = T(text, size=22).to_edge(DOWN, buff=0.35)
        new.set_max_width(config.frame_width - 1.0)
        if self.caption_mobj is None:
            self.play(FadeIn(new), run_time=0.5)
        else:
            self.play(ReplacementTransform(self.caption_mobj, new), run_time=0.5)
        self.caption_mobj = new

    def clear_stage(self, keep_caption=True):
        keep = {{self.caption_mobj}} if keep_caption and self.caption_mobj else set()
        gone = [m for m in self.mobjects if m not in keep]
        if gone:
            self.play(*[FadeOut(m) for m in gone], run_time=0.6)
        self.checklist = VGroup()
        self.checked = 0

    # ---------------------------------------------------------- beat 模板

    def beat_show_goal(self, narration, formulas, label="", **kw):
        self.clear_stage()
        title = T("目标", size=30, color=ACCENT, weight="BOLD").to_edge(UP, buff=0.6)
        goal = F(formulas[0], color=INK, scale=1.05)
        goal.set_max_width(config.frame_width - 1.5)
        self.play(FadeIn(title, shift=DOWN * 0.2), run_time=0.7)
        self.play(Write(goal), run_time=1.4)
        if narration:
            self.caption(narration)

    def beat_expand_definition(self, narration, parent, children, label="", **kw):
        self.clear_stage()
        top = F(parent, color=INK, scale=0.9).to_edge(UP, buff=0.7)
        top.set_max_width(config.frame_width - 1.5)
        self.play(FadeIn(top), run_time=0.8)
        items = VGroup()
        for i, ch in enumerate(children):
            box = T("□", size=26, color=AMBER)
            f = F(ch, color=INK, scale=0.8)
            row = VGroup(box, f).arrange(RIGHT, buff=0.3)
            items.add(row)
        items.arrange(DOWN, aligned_edge=LEFT, buff=0.45).next_to(top, DOWN, buff=0.8)
        items.set_max_width(config.frame_width - 2.0)
        for row in items:
            self.play(FadeIn(row, shift=RIGHT * 0.2), run_time=0.6)
        self.checklist = items
        self.checked = 0
        if narration:
            self.caption(narration)

    def beat_check_condition(self, narration, formulas, label="", **kw):
        if len(self.checklist) > self.checked:
            row = self.checklist[self.checked]
            check = T("✓", size=26, color=OK).move_to(row[0])
            evidence = F(formulas[0], color=ACCENT, scale=0.62)
            evidence.next_to(row, DOWN, aligned_edge=LEFT, buff=0.12)
            evidence.set_max_width(config.frame_width - 2.5)
            self.play(ReplacementTransform(row[0], check), run_time=0.5)
            row.submobjects[0] = check
            self.play(FadeIn(evidence), run_time=0.6)
            self.checked += 1
        else:
            self.clear_stage()
            head = T(label or "验证", size=26, color=ACCENT, weight="BOLD").to_edge(UP, buff=0.7)
            f = F(formulas[0], color=INK, scale=0.95)
            f.set_max_width(config.frame_width - 1.5)
            mark = T("✓", size=34, color=OK).next_to(f, RIGHT, buff=0.4)
            self.play(FadeIn(head), Write(f), run_time=1.2)
            self.play(FadeIn(mark), run_time=0.4)
        if narration:
            self.caption(narration)

    def beat_case_split(self, narration, formulas, label="", **kw):
        self.clear_stage()
        boxes = VGroup()
        for f in formulas:
            inner = F(f, color=INK, scale=0.7)
            rect = SurroundingRectangle(inner, color=ACCENT, buff=0.25, corner_radius=0.08)
            boxes.add(VGroup(rect, inner))
        boxes.arrange(RIGHT, buff=0.5)
        boxes.set_max_width(config.frame_width - 1.0)
        self.play(*[Create(b[0]) for b in boxes], run_time=0.8)
        self.play(*[FadeIn(b[1]) for b in boxes], run_time=0.8)
        if narration:
            self.caption(narration)

    def beat_formula_transform(self, narration, formulas, label="", **kw):
        self.clear_stage()
        src = F(formulas[0], color=INK, scale=0.95)
        src.set_max_width(config.frame_width - 1.5)
        self.play(Write(src), run_time=1.0)
        for nxt in formulas[1:]:
            dst = F(nxt, color=INK, scale=0.95)
            dst.set_max_width(config.frame_width - 1.5)
            self.play(ReplacementTransform(src, dst), run_time=1.1)
            src = dst
        if narration:
            self.caption(narration)

    def beat_counterexample(self, narration, formulas, label="", **kw):
        self.clear_stage()
        head = T(label or "反例", size=26, color=AMBER, weight="BOLD").to_edge(UP, buff=0.7)
        lines = VGroup(*[F(f, color=INK, scale=0.8) for f in formulas])
        lines.arrange(DOWN, aligned_edge=LEFT, buff=0.4)
        lines.set_max_width(config.frame_width - 2.5)
        frame = SurroundingRectangle(lines, color=AMBER, buff=0.35, corner_radius=0.1)
        self.play(FadeIn(head), run_time=0.5)
        self.play(Create(frame), run_time=0.7)
        for line in lines:
            self.play(FadeIn(line, shift=RIGHT * 0.15), run_time=0.55)
        if narration:
            self.caption(narration)

    def beat_conclude(self, narration, formulas, label="", **kw):
        self.clear_stage()
        f = F(formulas[0], color=INK, scale=1.0)
        f.set_max_width(config.frame_width - 1.5)
        box = SurroundingRectangle(f, color=OK, buff=0.35, corner_radius=0.1)
        self.play(Write(f), run_time=1.2)
        self.play(Create(box), run_time=0.8)
        if narration:
            self.caption(narration)

    # ---------------------------------------------------------- 时间轴

    def construct(self):
{timeline}
        self.wait(1.0)
'''


def generate_manim_code(spec: AnimationSpec) -> str:
    lines: list[str] = []
    for beat in spec.beats:
        p = dict(beat.params)
        narration = p.pop("narration", "")
        label = p.pop("label", "")
        formulas = p.pop("formulas", [])
        args = [f"narration={narration!r}"]
        if beat.beat_type == "expand_definition":
            args.append(f"parent={p.get('parent', formulas[0] if formulas else '')!r}")
            args.append(f"children={p.get('children', formulas[1:])!r}")
        else:
            args.append(f"formulas={formulas!r}")
        if label:
            args.append(f"label={label!r}")
        method = f"beat_{beat.beat_type}"
        lines.append(f"        self.{method}({', '.join(args)})")
        lines.append(f"        self.wait({beat.duration:.1f})")
    timeline = "\n".join(lines) if lines else "        pass"
    return PRELUDE.format(
        title=spec.title.replace('"""', "'''"),
        spec_id=spec.id,
        quality=spec.quality,
        scene_name=spec.scene_name,
        timeline=timeline,
    )
