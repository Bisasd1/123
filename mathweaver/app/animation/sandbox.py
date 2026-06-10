"""沙箱渲染:在子进程中执行 manim,带超时与优雅降级。

manim 未安装时不报错,任务以 source_only 状态完成——
源码永远可下载,视频是锦上添花。
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

QUALITY_FLAG = {"l": "-ql", "m": "-qm", "h": "-qh"}
QUALITY_DIR = {"l": "480p15", "m": "720p30", "h": "1080p60"}


def manim_available() -> bool:
    return importlib.util.find_spec("manim") is not None


def syntax_check(code: str) -> None:
    """生成代码必须先通过编译检查,失败属于 codegen bug,直接抛错。"""
    compile(code, "<generated_scene>", "exec")


def render(code: str, scene_name: str, out_dir: Path,
           quality: str = "m", timeout: int = 600, spec=None) -> dict:
    """渲染生成的场景代码。

    返回 {status, source_path, video_path?, log}:
      status: rendered | source_only | failed
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    syntax_check(code)
    source_path = out_dir / "scene.py"
    source_path.write_text(code, encoding="utf-8")

    if not manim_available():
        return {
            "status": "source_only",
            "source_path": str(source_path),
            "log": "环境未安装 manim,已生成可渲染源码;"
                   "安装 manim 后执行: manim render "
                   f"{QUALITY_FLAG.get(quality, '-qm')} scene.py {scene_name}",
        }

    with tempfile.TemporaryDirectory(prefix="mw_render_") as tmp:
        tmp_path = Path(tmp)
        work_file = tmp_path / "scene.py"
        work_file.write_text(code, encoding="utf-8")
        cmd = [sys.executable, "-m", "manim", "render",
               QUALITY_FLAG.get(quality, "-qm"), "--media_dir", str(tmp_path / "media"),
               str(work_file), scene_name]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout, cwd=tmp)
        except subprocess.TimeoutExpired:
            return {"status": "failed", "source_path": str(source_path),
                    "log": f"渲染超时(>{timeout}s),可降低质量重试(quality=l)。"}

        log = (proc.stdout or "") + "\n" + (proc.stderr or "")
        if proc.returncode != 0:
            return {"status": "failed", "source_path": str(source_path),
                    "log": log[-4000:]}

        video = _find_video(tmp_path / "media", scene_name, quality)
        if video is None:
            return {"status": "failed", "source_path": str(source_path),
                    "log": "manim 退出码 0 但未找到 mp4 产物。\n" + log[-2000:]}
        final = out_dir / "scene.mp4"
        shutil.copy2(video, final)
        return {"status": "rendered", "source_path": str(source_path),
                "video_path": str(final), "log": log[-2000:]}


def _find_video(media_dir: Path, scene_name: str, quality: str) -> Path | None:
    if not media_dir.exists():
        return None
    preferred = list(media_dir.glob(f"videos/*/{QUALITY_DIR.get(quality, '*')}/{scene_name}.mp4"))
    if preferred:
        return preferred[0]
    any_mp4 = sorted(media_dir.rglob(f"{scene_name}.mp4"))
    if any_mp4:
        return any_mp4[0]
    all_mp4 = sorted(media_dir.rglob("*.mp4"))
    return all_mp4[-1] if all_mp4 else None
