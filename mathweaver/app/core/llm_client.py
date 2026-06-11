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


class LLMAuthError(RuntimeError):
    """401/403:key 无效或无权限。重试无意义,立即失败并给出可操作的提示。"""


# base_url → 实际可用的 chat/completions 端点(进程内缓存,避免每次探测)
_resolved_endpoint: dict[str, str] = {}


def _endpoint_candidates(base_url: str) -> list[str]:
    """用户可能填 https://relay.com 或 https://relay.com/v1,两种都接受。"""
    base = base_url.rstrip("/")
    if base in _resolved_endpoint:
        return [_resolved_endpoint[base]]
    if base.endswith("/v1") or "/v1/" in base:
        return [base + "/chat/completions"]
    return [base + "/v1/chat/completions", base + "/chat/completions"]


# 部分中转站套了 Cloudflare:默认的 python-requests UA 会被人机验证拦截(403
# "Just a moment...")。统一带浏览器 UA 可绕过纯 UA 规则;若是 IP 级挑战则需换网络。
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "application/json",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def is_cloudflare_challenge(resp) -> bool:
    """403/503 且响应体是 Cloudflare 人机验证页(不是中转站自己的拒绝)。"""
    if resp.status_code not in (403, 503):
        return False
    text = (resp.text or "")[:2000]
    return ("Just a moment" in text or "cf-chl" in text
            or "challenge-platform" in text or "cloudflare" in text.lower())


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
        if not str(s["llm_api_key"]).isascii():
            raise LLMNotConfigured(
                "api_key 含非 ASCII 字符(疑似占位符未替换),请在设置中填入真实 key。")
        base = s["llm_base_url"].rstrip("/")
        candidates = _endpoint_candidates(base)
        headers = {**BROWSER_HEADERS,
                   "Authorization": f"Bearer {s['llm_api_key']}",
                   "Content-Type": "application/json; charset=utf-8"}
        # 显式 UTF-8 编码请求体:requests 的 json= 默认 ensure_ascii,
        # 中文会被转义成 \uXXXX,部分中转站按字符数计费/截断时会出问题。
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_err: Exception | None = None
        for attempt in range(int(s.get("llm_max_retries", 3))):
            for url in list(candidates):
                try:
                    resp = requests.post(url, headers=headers, data=body,
                                         timeout=float(s.get("llm_timeout", 120)),
                                         stream=stream)
                    if resp.status_code in (401, 403):
                        if is_cloudflare_challenge(resp):
                            raise RuntimeError(
                                "请求被 Cloudflare 人机验证拦截(非 key 问题)。"
                                "请改用该中转站的 API 专用域名(查看其控制台文档),"
                                "或从未被挑战的网络环境(如本地家庭网络)访问。")
                        raise LLMAuthError(
                            f"HTTP {resp.status_code}:API key 无效或无权限"
                            f"({resp.text[:160]})。请在设置中检查 base_url 与 api_key。")
                    if resp.status_code == 404 and len(candidates) > 1:
                        # 路径不对(如缺 /v1):换下一个候选端点,本轮不计入重试
                        candidates.remove(url)
                        last_err = RuntimeError(f"HTTP 404 at {url}")
                        continue
                    if resp.status_code in (429, 500, 502, 503, 504):
                        last_err = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                        break          # 跳出候选循环,走退避重试
                    resp.raise_for_status()
                    _resolved_endpoint[base] = url
                    return resp
                except (requests.Timeout, requests.ConnectionError) as e:
                    last_err = e
                    break
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
    repaired = _balance_truncated_json(t)
    if repaired is not None:
        return repaired
    raise ValueError("JSON 解析失败(可能被截断或含非法转义)")


def _balance_truncated_json(t: str) -> dict | list | None:
    """输出在中途被截断时,砍掉残缺的尾部并补齐未闭合的括号。"""
    # 截到最后一个完整的值边界(闭括号或引号后的逗号),再补闭合符
    cut = max(t.rfind("}"), t.rfind("]"), t.rfind('",'), t.rfind('"'))
    if cut <= 0:
        return None
    frag = t[:cut + 1].rstrip().rstrip(",")
    stack = []
    in_str = escape = False
    for ch in frag:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = in_str
        elif ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch in "{[":
                stack.append("}" if ch == "{" else "]")
            elif ch in "}]" and stack:
                stack.pop()
    if in_str:
        frag += '"'
    frag += "".join(reversed(stack))
    try:
        return json.loads(frag)
    except json.JSONDecodeError:
        return None
