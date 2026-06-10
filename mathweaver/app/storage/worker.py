"""渲染队列:线程池执行动画任务(manim 渲染是分钟级,必须异步)。"""
from __future__ import annotations

import traceback
from concurrent.futures import ThreadPoolExecutor

from app.animation import (build_storyboard, compile_storyboard,
                           generate_manim_code, render)
from app.core.config import get_settings
from app.core.llm_client import LLMClient
from app.ir import ProofGraph, StoryboardSpec
from app.storage import db

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mw_render")

REFINE_SYSTEM = """你是数学教学动画的旁白润色器。给定 storyboard JSON,
只允许修改每个 beat 的 narration(更口语、面向学生、≤60字),
其余字段原样保留。输出修改后的完整 storyboard JSON。"""


def submit_animation_job(conv_id: str, graph_dict: dict, conclusion_id: str,
                         quality: str | None = None) -> dict:
    job = db.create_job(conv_id, graph_dict["id"], "animation",
                        {"conclusion_id": conclusion_id,
                         "quality": quality or get_settings()["render_quality"]})
    _executor.submit(_run_animation_job, job["id"], graph_dict)
    return job


def _run_animation_job(job_id: str, graph_dict: dict) -> None:
    job = db.get_job(job_id)
    if not job:
        return
    params = job["params"]
    try:
        db.update_job(job_id, status="building_storyboard")
        graph = ProofGraph.from_dict(graph_dict)
        sb = build_storyboard(graph, params["conclusion_id"])
        sb = _maybe_refine_narration(sb)

        db.update_job(job_id, status="compiling")
        spec = compile_storyboard(sb, quality=params.get("quality", "m"))
        code = generate_manim_code(spec)

        db.update_job(job_id, status="rendering",
                      result={"storyboard": sb.to_dict(), "spec": spec.to_dict()})
        out_dir = db.job_artifact_dir(job_id)
        render_result = render(code, spec.scene_name, out_dir,
                               quality=spec.quality,
                               timeout=int(get_settings()["render_timeout"]),
                               spec=spec)

        status = {"rendered": "done", "source_only": "done_source_only",
                  "failed": "render_failed"}[render_result["status"]]
        db.update_job(job_id, status=status, result={
            "storyboard": sb.to_dict(), "spec": spec.to_dict(),
            "render": {k: v for k, v in render_result.items()},
            "has_video": render_result["status"] == "rendered",
        })
    except Exception as e:  # noqa: BLE001
        db.update_job(job_id, status="failed",
                      error=f"{e}\n{traceback.format_exc()[-1500:]}")


def _maybe_refine_narration(sb: StoryboardSpec) -> StoryboardSpec:
    """LLM 可用时润色旁白;任何失败都退回确定性版本。"""
    client = LLMClient()
    if not client.configured:
        return sb
    try:
        import json as _json
        data = client.complete_json(REFINE_SYSTEM,
                                    _json.dumps(sb.to_dict(), ensure_ascii=False),
                                    heavy=False, max_tokens=6000)
        refined = StoryboardSpec.from_dict(data)
        if len(refined.beats) == len(sb.beats) and \
           all(r.beat_type == o.beat_type and r.formulas == o.formulas
               for r, o in zip(refined.beats, sb.beats)):
            refined.id, refined.graph_id = sb.id, sb.graph_id
            refined.selected_node_ids = sb.selected_node_ids
            refined.built_by = "llm"
            return refined
    except Exception:
        pass
    return sb
