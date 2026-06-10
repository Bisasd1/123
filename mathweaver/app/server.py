"""Flask 应用工厂与全部 API 路由。

事件协议按"工具调用流"设计(AGTOOLS 风格):SSE 推送 tool_call /
tool_result / token / pipeline_done 事件。当前版本移除了 Manim 动画入口,把
ProofGraph 的后处理改成 MathScene/Exposition:对象表、图示、节点细节、追溯。
"""
from __future__ import annotations

import json
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_file

from app.core.config import public_settings, update_settings
from app.core.llm_client import LLMClient, LLMNotConfigured
from app.exposition import build_math_scene
from app.ir import GraphPatch, ProofGraph, apply_patch
from app.pipeline import debug_node, run_pipeline, synthesize_solution, trace_node
from app.storage import db
from app.validators import validate_all

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"

PROBLEM_HINTS = (
    "证明", "求", "谱", "算子", "spectrum", "operator", "prove", "\\", "∈",
    "ℓ", "lim", "极限", "矩阵", "空间", "勾股", "毕达哥拉斯", "直角三角形",
    "面积法", "Pythagorean", "right triangle",
    # 通用数学触发词:让任意证明/求解题都能进入结构化流水线
    "无理数", "有理数", "整数", "实数", "复数", "素数", "质数", "整除", "因子",
    "完备", "完备性", "收敛", "发散", "连续", "可微", "可导", "可积", "有界",
    "单调", "稠密", "可数", "不可数", "归纳", "反证", "存在", "唯一", "任意",
    "定理", "引理", "推论", "不等式", "等式", "恒等式", "数列", "级数", "函数",
    "集合", "子集", "映射", "群", "环", "域", "向量", "线性", "微分", "积分",
    "导数", "supremum", "infimum", "上确界", "下确界", "irrational", "rational",
    "converge", "continuous", "theorem", "lemma", "prime", "induction",
)

# 强触发:含这些词基本可断定是题目,直接进入流水线(不需要凑够 2 个提示词)
STRONG_PROBLEM_TRIGGERS = (
    "证明", "求证", "试证", "证：", "证:", "求解", "解方程", "prove that",
    "prove ", "show that", "证明：", "求证：",
)

CHAT_SYSTEM = (
    "你是 MathWeaver 的数学助手,擅长把数学证明解释成图谱、对象表、图示与追溯。"
    "回答简洁、面向学生。当前会话可能已生成证明图谱,用户的问题可结合图谱内容回答。"
)


def sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def looks_like_problem(text: str) -> bool:
    low = text.lower()
    if any(t.lower() in low for t in STRONG_PROBLEM_TRIGGERS):
        return True
    # "实数集合是完备的"这类短命题也应进入流水线:长度阈值放宽到 6
    return len(text) > 6 and sum(1 for h in PROBLEM_HINTS if h.lower() in low) >= 2


def create_app() -> Flask:
    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
    db.init_db()

    # ------------------------------------------------------------- 页面

    @app.get("/")
    def index():
        return send_file(STATIC_DIR / "index.html")

    # ------------------------------------------------------------- 会话

    @app.get("/api/conversations")
    def conversations():
        return jsonify(db.list_conversations())

    @app.post("/api/conversations")
    def new_conversation():
        title = (request.json or {}).get("title") or "新会话"
        return jsonify(db.create_conversation(title))

    @app.delete("/api/conversations/<conv_id>")
    def del_conversation(conv_id):
        db.delete_conversation(conv_id)
        return jsonify({"ok": True})

    @app.get("/api/conversations/<conv_id>/messages")
    def messages(conv_id):
        return jsonify(db.list_messages(conv_id))

    @app.get("/api/conversations/<conv_id>/graphs")
    def conv_graphs(conv_id):
        return jsonify(db.list_graphs(conv_id))

    @app.get("/api/conversations/<conv_id>/jobs")
    def conv_jobs(conv_id):  # legacy compatibility: animation jobs were removed.
        return jsonify([])

    # ------------------------------------------------------------- 对话(SSE)

    @app.post("/api/conversations/<conv_id>/messages")
    def post_message(conv_id):
        body = request.json or {}
        content = (body.get("content") or "").strip()
        mode = body.get("mode", "auto")
        if not content:
            return jsonify({"error": "content 不能为空"}), 400
        db.add_message(conv_id, "user", content)
        if len(db.list_messages(conv_id)) == 1:
            db.touch_conversation(conv_id, title=content[:30])

        run_problem = (mode == "problem") or (mode == "auto" and looks_like_problem(content))

        def stream():
            if run_problem:
                yield from _stream_pipeline(conv_id, content)
            else:
                yield from _stream_chat(conv_id, content)
            yield sse({"type": "stream_end"})

        return Response(
            stream(),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    def _stream_pipeline(conv_id: str, content: str):
        tool_log = []
        for ev in run_pipeline(content):
            # thinking 事件很多,不入库(否则撑爆 meta.tool_log);只记录结构性步骤
            if ev["type"] in ("tool_call", "tool_result", "pipeline_error"):
                tool_log.append({k: v for k, v in ev.items() if k != "graph"})
            if ev["type"] == "pipeline_done":
                graph_dict = ev["graph"]
                db.save_graph(conv_id, graph_dict)
                v = ev.get("validation", {})
                mode = ev.get("mode")
                mode_note = (
                    "\n\n> 当前为 **demo 模式**(未配置 LLM),加载的是内置黄金参照图谱。"
                    "在设置中填入中转站 API 后可对任意证明题生成结构图与解答。"
                    if mode == "golden_demo" else ""
                )
                ok = v.get("ok")
                # 第一步:先产出"结构图"本身(右侧多视图据此渲染)
                head = (
                    f"**结构图已生成:{graph_dict.get('title', '')}** — "
                    f"{len(graph_dict['nodes'])} 节点 / {len(graph_dict['edges'])} 边 / "
                    f"{'校验通过 ✓' if ok else str(len(v.get('errors', []))) + ' 处待复核'}。\n"
                    "右侧可查看 总览 / 图谱 / 图示 / 对象 / 节点 / 追溯。下面据此重建完整解答 ↓"
                    f"{mode_note}"
                )
                db.add_message(
                    conv_id, "assistant", head,
                    meta={"graph_id": graph_dict["id"], "tool_log": tool_log, "kind": "structure"},
                )
                yield sse({
                    "type": "pipeline_done", "graph_id": graph_dict["id"],
                    "validation": v, "mode": mode, "summary": head,
                })

                # 第二步:据刚生成的结构图,流式重建完整、流畅的解答
                yield sse({"type": "tool_call", "name": "compose_solution",
                           "label": "据结构图重建完整解答"})
                parts: list[str] = []
                try:
                    graph_obj = ProofGraph.from_dict(graph_dict)
                    for piece in synthesize_solution(graph_obj):
                        parts.append(piece)
                        yield sse({"type": "token", "content": piece})
                except GeneratorExit:
                    # 客户端断开(如刷新页面):仍把已生成的部分落库,避免"刷新什么都没了"
                    db.add_message(conv_id, "assistant",
                                   "".join(parts).strip() or "(解答生成被中断)",
                                   meta={"graph_id": graph_dict["id"], "kind": "solution"})
                    raise
                except Exception as e:  # noqa: BLE001 —— 解答失败不得中断流;给出可见反馈
                    msg = f"\n\n(解答生成出错:{e})"
                    parts.append(msg)
                    yield sse({"type": "token", "content": msg})
                solution = "".join(parts).strip() or "(未能生成解答)"
                db.add_message(conv_id, "assistant", solution,
                               meta={"graph_id": graph_dict["id"], "kind": "solution"})
                yield sse({"type": "tool_result", "name": "compose_solution",
                           "summary": "完整解答已生成"})
            elif ev["type"] == "pipeline_error":
                db.add_message(
                    conv_id,
                    "assistant",
                    f"流水线在 {ev['stage']} 阶段失败:{ev['message']}",
                    meta={"tool_log": tool_log},
                )
                yield sse(ev)
            else:
                yield sse(ev)

    def _stream_chat(conv_id: str, content: str):
        client = LLMClient()
        history = db.list_messages(conv_id)[-10:]
        msgs = [{"role": "system", "content": CHAT_SYSTEM}]
        msgs += [{"role": m["role"], "content": m["content"]} for m in history]
        if not client.configured:
            reply = (
                "当前为 demo 模式(未配置 LLM)。我可以:\n"
                "1. 粘贴一道**乘法/对角算子谱分类**题目 → 生成内置黄金证明图谱与 MathScene;\n"
                "2. 粘贴一道**勾股定理面积法**题目 → 生成几何面积图示与节点细节;\n"
                "3. 在右侧查看**总览 / 图谱 / 对象 / 图示 / 节点 / 追溯**。\n"
                "在左下角设置中填入中转站 base_url 与 api_key 后解锁完整能力。"
            )
            db.add_message(conv_id, "assistant", reply)
            yield sse({"type": "token", "content": reply})
            return
        full = []
        try:
            for piece in client.chat_stream(msgs):
                full.append(piece)
                yield sse({"type": "token", "content": piece})
        except (RuntimeError, LLMNotConfigured) as e:
            err = f"LLM 调用失败:{e}"
            full = [err]
            yield sse({"type": "token", "content": err})
        db.add_message(conv_id, "assistant", "".join(full))

    # ------------------------------------------------------------- 图谱 / MathScene

    @app.get("/api/graphs/<graph_id>")
    def get_graph(graph_id):
        g = db.get_graph(graph_id)
        if not g:
            return jsonify({"error": "graph 不存在"}), 404
        graph = ProofGraph.from_dict(g)
        return jsonify({"graph": g, "depths": graph.topo_depths(), "validation": validate_all(graph)})

    @app.get("/api/graphs/<graph_id>/explain")
    def explain_graph(graph_id):
        g = db.get_graph(graph_id)
        if not g:
            return jsonify({"error": "graph 不存在"}), 404
        graph = ProofGraph.from_dict(g)
        scene = build_math_scene(graph)
        return jsonify({"scene": scene.to_dict()})

    @app.get("/api/graphs/<graph_id>/trace/<node_id>")
    def trace(graph_id, node_id):
        g = db.get_graph(graph_id)
        if not g:
            return jsonify({"error": "graph 不存在"}), 404
        try:
            return jsonify(trace_node(ProofGraph.from_dict(g), node_id))
        except ValueError as e:
            return jsonify({"error": str(e)}), 404

    @app.post("/api/graphs/<graph_id>/nodes/<node_id>/debug")
    def debug(graph_id, node_id):
        g = db.get_graph(graph_id)
        if not g:
            return jsonify({"error": "graph 不存在"}), 404
        question = (request.json or {}).get("question", "").strip()
        if not question:
            return jsonify({"error": "question 不能为空"}), 400
        try:
            result = debug_node(ProofGraph.from_dict(g), node_id, question)
        except ValueError as e:
            return jsonify({"error": str(e)}), 404
        new_graph = result.pop("new_graph", None)
        if new_graph is not None:
            conv = db.graph_conv(graph_id)
            db.save_graph(conv, new_graph.to_dict())
            result["new_graph_id"] = new_graph.id
        conv = db.graph_conv(graph_id)
        if conv:
            db.add_message(
                conv,
                "assistant",
                f"**节点调试** `{node_id}`:{question}\n\n{result['answer']}",
                meta={"debug_node": node_id, "new_graph_id": result.get("new_graph_id")},
            )
        return jsonify(result)

    @app.post("/api/graphs/<graph_id>/patch")
    def patch_graph(graph_id):
        g = db.get_graph(graph_id)
        if not g:
            return jsonify({"error": "graph 不存在"}), 404
        patch = GraphPatch.from_dict(request.json or {})
        new_graph, invalidated = apply_patch(ProofGraph.from_dict(g), patch)
        report = validate_all(new_graph)
        conv = db.graph_conv(graph_id)
        db.save_graph(conv, new_graph.to_dict())
        return jsonify({"new_graph_id": new_graph.id, "invalidated": invalidated, "validation": report})

    # ------------------------------------------------------------- 解释层 / 旧动画入口

    @app.post("/api/graphs/<graph_id>/animate")
    def animate_removed(graph_id):
        # 动画功能已移除;保留 410 以便旧前端/旧客户端得到明确错误。
        if not db.get_graph(graph_id):
            return jsonify({"error": "graph 不存在"}), 404
        return jsonify({
            "error": "动画功能已移除。请使用 /api/graphs/<graph_id>/explain 获取 MathScene 完整图景。"
        }), 410

    # ------------------------------------------------------------- 设置

    @app.get("/api/settings")
    def get_settings_api():
        return jsonify(public_settings())

    @app.post("/api/settings")
    def post_settings():
        update_settings(request.json or {})
        return jsonify(public_settings())

    return app
