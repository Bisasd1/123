"""逻辑链动画:情形C ⇒ 连续谱

由 MathWeaver 确定性 codegen 生成(AnimationSpec id: anim_6b9ca2e99a)。
渲染:  manim render -qm {filename} ProofScene
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


class ProofScene(Scene):
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
        keep = {self.caption_mobj} if keep_caption and self.caption_mobj else set()
        gone = [m for m in self.mobjects if m not in keep]
        if gone:
            self.play(*[FadeOut(m) for m in gone], run_time=0.6)
        self.checklist = VGroup()
        self.checked = 0

    # ---------------------------------------------------------- beat 模板

    def beat_show_goal(self, narration, formulas, label="", **kw):
        self.clear_stage()
        title = T("目标", size=30, color=ACCENT, weight="BOLD").to_edge(UP, buff=0.6)
        goal = MathTex(formulas[0], color=INK).scale(1.05)
        goal.set_max_width(config.frame_width - 1.5)
        self.play(FadeIn(title, shift=DOWN * 0.2), run_time=0.7)
        self.play(Write(goal), run_time=1.4)
        if narration:
            self.caption(narration)

    def beat_expand_definition(self, narration, parent, children, label="", **kw):
        self.clear_stage()
        top = MathTex(parent, color=INK).scale(0.9).to_edge(UP, buff=0.7)
        top.set_max_width(config.frame_width - 1.5)
        self.play(FadeIn(top), run_time=0.8)
        items = VGroup()
        for i, ch in enumerate(children):
            box = T("□", size=26, color=AMBER)
            f = MathTex(ch, color=INK).scale(0.8)
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
            evidence = MathTex(formulas[0], color=ACCENT).scale(0.62)
            evidence.next_to(row, DOWN, aligned_edge=LEFT, buff=0.12)
            evidence.set_max_width(config.frame_width - 2.5)
            self.play(ReplacementTransform(row[0], check), run_time=0.5)
            row.submobjects[0] = check
            self.play(FadeIn(evidence), run_time=0.6)
            self.checked += 1
        else:
            self.clear_stage()
            head = T(label or "验证", size=26, color=ACCENT, weight="BOLD").to_edge(UP, buff=0.7)
            f = MathTex(formulas[0], color=INK).scale(0.95)
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
            inner = MathTex(f, color=INK).scale(0.7)
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
        src = MathTex(formulas[0], color=INK).scale(0.95)
        src.set_max_width(config.frame_width - 1.5)
        self.play(Write(src), run_time=1.0)
        for nxt in formulas[1:]:
            dst = MathTex(nxt, color=INK).scale(0.95)
            dst.set_max_width(config.frame_width - 1.5)
            self.play(ReplacementTransform(src, dst), run_time=1.1)
            src = dst
        if narration:
            self.caption(narration)

    def beat_counterexample(self, narration, formulas, label="", **kw):
        self.clear_stage()
        head = T(label or "反例", size=26, color=AMBER, weight="BOLD").to_edge(UP, buff=0.7)
        lines = VGroup(*[MathTex(f, color=INK).scale(0.8) for f in formulas])
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
        f = MathTex(formulas[0], color=INK).scale(1.0)
        f.set_max_width(config.frame_width - 1.5)
        box = SurroundingRectangle(f, color=OK, buff=0.35, corner_radius=0.1)
        self.play(Write(f), run_time=1.2)
        self.play(Create(box), run_time=0.8)
        if narration:
            self.caption(narration)

    # ---------------------------------------------------------- 时间轴

    def construct(self):
        self.beat_show_goal(narration='目标:情形C ⇒ 连续谱(当前假设:\\lambda\\notin A;  \\inf_n|a_n-\\lambda|=0)', formulas=['\\lambda\\in\\overline A\\setminus A \\implies \\lambda\\in\\sigma_c(M_a)'])
        self.wait(3.0)
        self.beat_expand_definition(narration='展开『连续谱 σ_c(T)』的定义:需要逐一验证下列条件。', parent='\\lambda\\in\\overline A\\setminus A \\implies \\lambda\\in\\sigma_c(M_a)', children=['T-\\lambda I \\text{ 单射}', '\\overline{R(T-\\lambda I)}=H', 'R(T-\\lambda I)\\neq H'], label='DEF-continuous-spectrum')
        self.wait(4.0)
        self.beat_check_condition(narration='核引理 + 每项非零 ⇒ 单射。连续谱定义的条件一。', formulas=['\\ker(M_a-\\lambda I)=\\{0\\}'], label='情形C:单射 ✓')
        self.wait(3.5)
        self.beat_check_condition(narration='Z_λ=∅ 时值域闭包没有任何坐标约束;有限支撑向量都在值域中。条件二。', formulas=['\\overline{R(M_a-\\lambda I)}=\\ell^2'], label='情形C:值域稠密 ✓')
        self.wait(3.5)
        self.beat_check_condition(narration='取 a_{n_k}→λ,令 y_{n_k}=a_{n_k}−λ(衰减后属于 ℓ²),解被迫 x_{n_k}=1,不可平方求和。条件三。', formulas=['\\inf_n|a_n-\\lambda|=0 \\implies R(M_a-\\lambda I)\\neq\\ell^2'], label='情形C:值域 ≠ ℓ² ✓')
        self.wait(3.5)
        self.beat_counterexample(narration='易错点:不能由此推出 inf|a_n-λ|>0', formulas=['a_n = \\tfrac{1}{n}', '\\lambda = 0', 'a_n - \\lambda \\neq 0\\ \\forall n', '\\inf_n |a_n-\\lambda| = 0'], label='a_n = 1/n')
        self.wait(4.0)
        self.beat_conclude(narration='为什么不是 σ_c={λ: inf=0}?因为 λ∈A 时算子不单射,落入点谱——必须排除 A。', formulas=['\\lambda\\in\\overline A\\setminus A \\implies \\lambda\\in\\sigma_c(M_a)'])
        self.wait(3.0)
        self.wait(1.0)
