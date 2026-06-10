# MathScene 改造说明

本版本按“移除动画功能,改成数学问题完整图景”的方向完成改造。

## 已改动

- 移除前端动画入口:不再显示 Manim/生成视频按钮,主界面改为“总览 / 图谱 / 对象 / 图示 / 节点 / 追溯”。
- 后端新增 `GET /api/graphs/<gid>/explain`,返回 `MathSceneSpec`。
- 旧 `POST /api/graphs/<gid>/animate` 保留为兼容入口,但固定返回 HTTP 410,提示改用 `/explain`。
- 新增 `app/exposition/scene.py`,确定性把 `ProofGraph` 转为:
  - `ObjectRegistry` 对象表
  - `StepCapsule` 节点细节胶囊
  - `DiagramSpec` 静态 SVG 图示描述
  - `VisualBinding` 公式项与图形对象绑定
  - `ConceptTrace` 基础定义/引理追溯
  - `ExampleSpec` / `CounterexampleSpec`
- 扩展 IR 节点类型,支持 `goal / setup / construction / lemma / calculation / condition_check / case_split / conclusion` 等。
- 新增几何基础库 `foundations_geometry.json`。
- 新增勾股定理面积法 pattern 与黄金图谱:
  - `app/registry/data/patterns/pythagorean_area_proof.json`
  - `app/registry/data/golden/pythagorean_area_graph.json`
- Pipeline demo 模式现在支持两类题:
  - 对角/乘法算子谱分类
  - 勾股定理面积法
- `requirements.txt` 不再依赖 Manim / LaTeX / ffmpeg。
- `tests/run_all.py` 已更新并覆盖 MathScene、`/explain`、勾股 pattern、旧动画 API 禁用状态。

## 第二阶段:开放域证明 + 据结构图重建解答 + 思考进度

在“只支持 2 个注册模式”的基础上,扩展为“任意证明题 → 结构图 → 完整解答”,并解决长耗时无反馈的问题。

- **开放域通用证明生成**:输入未命中注册模式且已配置 LLM 时,走新路径
  `parse → LLM 规划"证明结构计划"(简单 JSON) → 确定性组装成 ProofGraph → 三层校验`。
  - 关键设计:不让 LLM 直接吐受严格枚举约束的 ProofGraph,而是产出更易写对的“计划”,
    由 `_plan_to_graph` 掌控节点类型/边关系/枚举/无环性,**产物默认即通过结构校验**(稳定性核心)。
  - 新增 `iter_generate_graph_open / generate_graph_open / _plan_to_graph / synthesize_solution`。
- **本题级基础锚点**:`ProofGraph.local_foundations` 让开放域证明显式声明自己用到的定义/公理/定理。
  - 校验层:合法来源集合 = Registry ∪ local_foundations;**注册模式图的 local_foundations 为空,
    “禁止发明来源”的保证对它们依旧成立**。发明未声明来源仍被判错。
  - 开放域只跑领域无关的 `conclusion_traceability` lint(无法对任意数学做谱/几何专用 lint)。
- **据结构图重建解答**:`/api/.../messages` 流水线先产出**结构图**,再 `synthesize_solution`
  按结构图节点顺序**流式重建**一篇完整、流畅、严谨的解答(LLM 不可用时确定性拼装兜底)。
- **模型思考进度**:结构规划阶段改为**流式**,新增 `thinking` SSE 事件,前端显示实时“规划进度”面板,
  解决“长时间无反馈 → 误以为卡死 → 刷新丢失”的问题。
- **刷新/断连健壮性**:结构图在 `pipeline_done` 即落库;解答流被 `GeneratorExit` 中断(如刷新)时
  也会把已生成部分落库;`thinking` 事件不写入 `meta.tool_log` 以免撑爆历史。
- **问题识别增强**:`looks_like_problem` 增加“证明/求证/...”强触发词与大量通用数学提示词,
  让“证明 √2 是无理数”“实数集合是完备的”等也进入结构化流水线。
- LLM 客户端对接中转站 `https://cmdme.cn/v1`(OpenAI 兼容),heavy=`gpt-5.5`、light=`gpt-5.4-mini`。

### 第二阶段验证

- `python tests/run_all.py` → **54 / 54 全部通过**(新增开放域 12 项:确定性组装/校验/
  本题基础不算悬空/发明来源仍被抓/据结构图重建解答/通用 MathScene/追溯解析)。
- 联网端到端实测(走真实 `run_pipeline` 流式路径):
  - “证明 √2 是无理数” → 10 节点 / 13 边 / 8 基础,校验 0 错误,解答 ~1600 字。
  - “证明实数集合是完备的” → 6 节点 / 9 边 / 5 基础,校验 0 错误,解答 ~2300 字。
  - 规划阶段流式输出 ~1 万字“思考进度”;gpt-5.5 规划耗时约 1–2 分钟(可在设置改用更快模型)。

## 验证(第一阶段)

已在项目根目录运行:

```bash
python tests/run_all.py
python -m compileall -q app tests
node --check /tmp/mathweaver_index_script.js
```

测试结果:42 / 42 全部通过。
