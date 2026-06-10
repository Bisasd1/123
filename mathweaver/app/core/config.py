"""配置:环境变量为底,var/settings.json 运行时覆盖(设置页可改,即时生效)。"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

VAR_DIR = Path(os.environ.get("MW_VAR_DIR", Path(__file__).resolve().parents[2] / "var"))
SETTINGS_FILE = VAR_DIR / "settings.json"
_lock = threading.Lock()

DEFAULTS = {
    "llm_base_url": os.environ.get("LLM_BASE_URL", ""),       # 例: https://中转站/v1
    "llm_api_key": os.environ.get("LLM_API_KEY", ""),
    "llm_model_heavy": os.environ.get("LLM_MODEL_HEAVY",
                                      os.environ.get("LLM_MODEL", "claude-sonnet-4-5")),
    "llm_model_light": os.environ.get("LLM_MODEL_LIGHT",
                                      os.environ.get("LLM_MODEL", "claude-sonnet-4-5")),
    "llm_stream": os.environ.get("LLM_STREAM", "true").lower() != "false",
    "llm_timeout": float(os.environ.get("LLM_TIMEOUT", "120")),
    "llm_max_retries": int(os.environ.get("LLM_MAX_RETRIES", "3")),
    "render_quality": os.environ.get("RENDER_QUALITY", "m"),
    "render_timeout": int(os.environ.get("RENDER_TIMEOUT", "600")),
}

MUTABLE_KEYS = set(DEFAULTS)
SECRET_KEYS = {"llm_api_key"}


def get_settings() -> dict:
    merged = dict(DEFAULTS)
    if SETTINGS_FILE.exists():
        try:
            merged.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    return merged


def update_settings(patch: dict) -> dict:
    with _lock:
        current = {}
        if SETTINGS_FILE.exists():
            try:
                current = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                current = {}
        for k, v in patch.items():
            if k in MUTABLE_KEYS and v is not None:
                current[k] = v
        VAR_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(current, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    return get_settings()


def public_settings() -> dict:
    s = get_settings()
    out = {k: v for k, v in s.items() if k not in SECRET_KEYS}
    out["llm_api_key_set"] = bool(s.get("llm_api_key"))
    out["llm_configured"] = bool(s.get("llm_base_url") and s.get("llm_api_key"))
    return out
