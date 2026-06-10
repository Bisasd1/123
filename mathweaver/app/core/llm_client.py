"""OpenAI 兼容协议的 LLM 客户端,面向中转站设计。

- 不依赖 openai SDK,只用 requests(协议层面完全兼容)。
- JSON 任务:剥围栏 → json.loads → 失败把报错回传重试(中转站常不支持 response_format)。
- 指数退避重试;流式可降级。
- heavy/light 双模型位:建图用强模型,分类等轻任务可换便宜模型。
"""
from __future__ import annotations

import json
import re
import time

import requests

from app.core.config import get_settings


class LLMNotConfigured(RuntimeError):
    pass


class LLMClient:
    def __init__(self):
        self.s = get_settings()

    # ------------------------------------------------------------- 基础

    @property
    def configured(self) -> bool:
        s = get_settings()
        return bool(s.get("llm_base_url") and s.get("llm_api_key"))

    def _post(self, payload: dict, stream: bool = False) -> requests.Response:
        s = get_settings()
        if not (s.get("llm_base_url") and s.get("llm_api_key")):
            raise LLMNotConfigured("未配置 LLM(base_url / api_key),当前为 demo 模式")
        url = s["llm_base_url"].rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {s['llm_api_key']}",
                   "Content-Type": "application/json; charset=utf-8"}
        # 显式 UTF-8 编码请求体:requests 的 json= 默认 ensure_ascii,
        # 中文会被转义成 \uXXXX,部分中转站按字符数计费/截断时会出问题。
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_err: Exception | None = None
        for attempt in range(int(s.get("llm_max_retries", 3))):
            try:
                resp = requests.post(url, headers=headers, data=body,
                                     timeout=float(s.get("llm_timeout", 120)),
                                     stream=stream)
                if resp.status_code in (429, 500, 502, 503, 504):
                    last_err = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                    time.sleep(min(2 ** attempt * 1.5, 12))
                    continue
                resp.raise_for_status()
                return resp
            except (requests.Timeout, requests.ConnectionError) as e:
                last_err = e
                time.sleep(min(2 ** attempt * 1.5, 12))
        raise RuntimeError(f"LLM 请求失败(已重试): {last_err}")

    # ------------------------------------------------------------- 普通对话

    def chat(self, messages: list[dict], heavy: bool = False,
             temperature: float = 0.4, max_tokens: int = 2048) -> str:
        s = get_settings()
        model = s["llm_model_heavy"] if heavy else s["llm_model_light"]
        resp = self._post({"model": model, "messages": messages,
                           "temperature": temperature, "max_tokens": max_tokens})
        data = resp.json()
        return data["choices"][0]["message"]["content"] or ""

    def chat_stream(self, messages: list[dict], heavy: bool = False,
                    temperature: float = 0.5, max_tokens: int = 2048):
        """逐 token 产出;中转站不支持 stream 时整段降级。"""
        s = get_settings()
        model = s["llm_model_heavy"] if heavy else s["llm_model_light"]
        if not s.get("llm_stream", True):
            yield self.chat(messages, heavy, temperature, max_tokens)
            return
        payload = {"model": model, "messages": messages, "temperature": temperature,
                   "max_tokens": max_tokens, "stream": True}
        resp = self._post(payload, stream=True)
        # SSE 响应通常不带 charset,requests 会按 ISO-8859-1 解码导致中文乱码;
        # 强制按 UTF-8 解码。
        resp.encoding = "utf-8"
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data:"):
                continue
            chunk = raw[5:].strip()
            if chunk == "[DONE]":
                break
            try:
                delta = json.loads(chunk)["choices"][0].get("delta", {})
                piece = delta.get("content")
                if piece:
                    yield piece
            except (json.JSONDecodeError, KeyError, IndexError):
                continue

    # ------------------------------------------------------------- 结构化 JSON

    def complete_json(self, system: str, user: str, heavy: bool = True,
                      max_tokens: int = 8000, json_retries: int = 2) -> dict | list:
        """强制 JSON 输出;解析失败把错误回传给模型修复。一律非流式。"""
        messages = [
            {"role": "system",
             "content": system + "\n\n输出要求:只输出一个合法 JSON,"
                                 "不要 markdown 围栏,不要任何解释文字。"},
            {"role": "user", "content": user},
        ]
        last_text = ""
        for _ in range(json_retries + 1):
            last_text = self.chat(messages, heavy=heavy, temperature=0.2,
                                  max_tokens=max_tokens)
            try:
                return extract_json(last_text)
            except ValueError as e:
                messages.append({"role": "assistant", "content": last_text[:6000]})
                messages.append({"role": "user",
                                 "content": f"上面的输出不是合法 JSON:{e}。"
                                            "请重新只输出修正后的 JSON。"})
        raise ValueError(f"LLM 多次未能产出合法 JSON,最后输出片段: {last_text[:300]}")


def extract_json(text: str) -> dict | list:
    """剥 ```json 围栏 / 截取最外层括号,然后解析。"""
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", t, re.DOTALL)
    if fence:
        t = fence.group(1).strip()
    if not t.startswith(("{", "[")):
        start = min((i for i in (t.find("{"), t.find("[")) if i >= 0), default=-1)
        if start < 0:
            raise ValueError("文本中找不到 JSON 起始符")
        t = t[start:]
    # 配平截断:从尾部回退到最后一个闭合括号
    for end in range(len(t), max(len(t) - 2000, 0), -1):
        frag = t[:end].rstrip()
        if frag.endswith(("}", "]")):
            try:
                return json.loads(frag)
            except json.JSONDecodeError:
                continue
    raise ValueError("JSON 解析失败(可能被截断或含非法转义)")
