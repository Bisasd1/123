# MathWeaver — 数学解释空间

MathWeaver 把数学问题编译成**可验证 ProofGraph**,再把 ProofGraph 编译成**MathScene 多视图解释空间**。

动画/Manim 渲染入口已经移除。新的主输出不是线性视频,而是同时展示:

- 证明主干图谱 ProofGraph
- 数学对象表 ObjectRegistry
- 节点细节胶囊 StepCapsule
- 静态 SVG 图示 DiagramSpec
- 公式项与图形对象绑定 VisualBinding
- 定义/引理追溯 ConceptTrace
- 例子、反例和边界提醒

```text
Problem Text
  → ProblemSpec + SymbolTable      (Layer 1  理解:识别模式、对齐记号)
  → ProofGraph Candidate           (Layer 2  构图:LLM 填充模板槽位)
  → CheckedProofGraph              (Layer 3  校验:schema / 图结构 / 数学 lint)
  → MathSceneSpec                  (Layer 4  解释:对象表 / 图示 / 节点合同 / 追溯)
  → Web UI                         (多视图:总览 / 图谱 / 对象 / 图示 / 节点 / 追溯)
```

核心原则:

1. **ProofGraph 只管推理依赖**,不要把所有图形微操作塞进主图。
2. **StepCapsule 记录每一步的数学合同**:输入、动作/命题、保证、合法性、输出、后续用途。
3. **ObjectRegistry 记录符号与对象来源**:对象类型、定义、性质、引入节点。
4. **DiagramSpec 记录可视化对象**,VisualBinding 把公式项和图形区域绑定。
5. **LLM 不写前端图示/动画代码**;后端确定性生成 MathScene JSON 与 SVG。

---

## 快速开始

```bash
pip install -r requirements.txt
python run.py                       # → http://127.0.0.1:5000
```

不配置任何 API 也可体验 demo 模式。支持两个内置黄金图谱:

### 1. 对角/乘法算子谱分类

```text
设 M_a x=(a_n x_n) 为 ℓ² 上的乘法算子,求其点谱、连续谱、剩余谱与预解集。
```

会加载内置 25 节点黄金图谱,并生成复平面谱分类图示。

### 2. 勾股定理面积法

```text
用面积法证明勾股定理,设直角三角形两直角边为 a,b,斜边为 c。
```

会加载勾股定理面积法黄金图谱,并生成外部正方形、四个全等三角形、中心正方形的 SVG 图示。

---

## 接入中转站

左下角「设置」填入 OpenAI 兼容中转站的 `base_url`(以 `/v1` 结尾)与 `api_key`,即时生效、落盘到 `var/settings.json`;也可用 `.env`(见 `.env.example`)。

协议层用 `requests` 直连 `/chat/completions`,带指数退避重试与 JSON 修复循环,不依赖厂商专属功能(response_format 等),对中转站最大兼容。

---

## 运行测试

```bash
python tests/run_all.py
```

测试覆盖:

- Registry 加载与 pattern 匹配
- 对角算子黄金图谱校验
- 勾股定理面积法黄金图谱校验
- 数学 lint 病灶注入
- 追溯与 GraphPatch 失效传播
- MathScene 构建与 `/explain` API
- Flask SSE 端到端

---

## 架构

```text
app/
├── ir/
│   ├── proof_graph.py     # ProofGraph: contexts + nodes + typed edges + obligations + symbols
│   └── specs.py           # ProblemSpec + MathSceneSpec + ObjectRegistry + StepCapsule + DiagramSpec
├── exposition/
│   └── scene.py           # ProofGraph → MathSceneSpec,确定性构建对象表/图示/细节胶囊
├── registry/
│   └── data/
│       ├── foundations_functional_analysis.json
│       ├── foundations_geometry.json
│       ├── patterns/diagonal_operator.json
│       ├── patterns/pythagorean_area_proof.json
│       └── golden/*.json
├── validators/            # schema → 图结构 → 数学 lint
├── pipeline/              # parse / generate / trace / debug
├── core/                  # 配置 + LLM 客户端(OpenAI 兼容)
├── storage/               # sqlite3 会话/消息/图谱版本
└── server.py              # Flask:SSE 工具调用事件流 + REST + /explain
static/index.html          # 单文件前端:总览 / 图谱 / 对象 / 图示 / 节点 / 追溯
```

---

## API 速览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/conversations` | 新建会话 |
| GET/DELETE | `/api/conversations[/<id>]` | 列表 / 删除 |
| GET | `/api/conversations/<id>/messages` | 历史消息 |
| POST | `/api/conversations/<id>/messages` | 发消息,**SSE** 返回(`mode`: auto/problem/chat) |
| GET | `/api/graphs/<gid>` | 图谱 + 深度 + 校验报告 |
| GET | `/api/graphs/<gid>/explain` | MathScene 多视图解释 JSON |
| GET | `/api/graphs/<gid>/trace/<nid>` | 追溯链 + foundation 锚点 |
| POST | `/api/graphs/<gid>/nodes/<nid>/debug` | 调试追问 `{question}` → 回答(+可选新版本图) |
| POST | `/api/graphs/<gid>/patch` | 手动打补丁(GraphPatch JSON) |
| GET/POST | `/api/settings` | 运行时设置(key 只写不读) |

旧动画 API `/api/graphs/<gid>/animate` 已禁用并返回 410；会话 jobs 列表保留兼容空响应。

---

## v1 边界

- 默认内置两个 pattern:对角/乘法算子谱分类、勾股定理面积法。
- `statement_canonical` 是封闭枚举的浅结构,服务于 lint 与查重,不是形式化证明系统。
- 数学 lint 仍是模式级规则集,不做通用定理证明。
- 图示当前是确定性 SVG 模板;后续可以把 DiagramSpec 路由到 Penrose / JSXGraph / Plotly / three.js 等后端。
