"""真实中转站 API 联调:python scripts/live_api_check.py --base https://cmdme.cn --key sk-xxx

验证链路(全部走真实 LLM):
  1. 端点探测:/models(自动尝试补 /v1),并把可用配置写入 var/settings.json
  2. 最小对话 ping
  3. 端到端:题目 → 结构图(先) → 三层校验 → 据结构图重建完整解答(后)
     内置两道验收题:√2 无理数、实数完备性;可用 --problem 追加任意题。

不带参数时从环境变量 LLM_BASE_URL / LLM_API_KEY 或 var/settings.json 读取。
key 只落在 gitignore 的 var/ 目录,绝不写进仓库文件。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests  # noqa: E402

PROBLEMS = [
    ("sqrt2", "证明根号2是无理数"),
    ("completeness", "证明实数集合是完备的:任何有上界的非空实数子集都有上确界。"),
]

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = ""):
    (PASSED if cond else FAILED).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"  [{detail}]" if detail and not cond else ""))


def probe_models(base: str, key: str) -> tuple[str, list[str]]:
    """返回 (可用 base_url, 模型 id 列表)。自动尝试补 /v1,带浏览器 UA 防 CF 误拦。"""
    from app.core.llm_client import BROWSER_HEADERS, is_cloudflare_challenge
    headers = {**BROWSER_HEADERS, "Authorization": f"Bearer {key}"}
    cands = [base.rstrip("/")]
    if not cands[0].endswith("/v1"):
        cands.insert(0, cands[0] + "/v1")
    cf_blocked = False
    for b in cands:
        try:
            r = requests.get(b + "/models", headers=headers, timeout=20)
            if r.status_code == 200:
                ids = [m.get("id") for m in r.json().get("data", [])]
                return b, ids
            if is_cloudflare_challenge(r):
                cf_blocked = True
                print(f"  [{b}/models] HTTP {r.status_code}: Cloudflare 人机验证页(非 key 问题)")
            else:
                print(f"  [{b}/models] HTTP {r.status_code}: {r.text[:120]}")
        except requests.RequestException as e:
            print(f"  [{b}/models] {type(e).__name__}: {str(e)[:120]}")
    if cf_blocked:
        print("\n  ⚠ 当前网络环境被该站点的 Cloudflare 防护拦截。可尝试:")
        print("    1. 登录中转站控制台,查看文档给出的「API 专用域名」(常见为 api.xxx 或另一个直连域名),")
        print("       用 --base 改填那个域名;")
        print("    2. 在本地电脑(家庭/手机网络)运行本脚本——数据中心/海外 IP 最容易被挑战;")
        print("    3. 部分站点提供免 CF 的备用端口/线路,详见其公告。")
    return "", []


def pick_model(ids: list[str], preferred: str) -> str:
    if preferred and (not ids or preferred in ids):
        return preferred
    if preferred:
        # 模糊匹配:用户给的名字按 token 拆开,找包含全部 token 的真实模型 id
        tokens = [t for t in re.split(r"[\s\-_./]+", preferred.lower()) if t]
        fuzzy = [i for i in ids if i and all(t in i.lower() for t in tokens)]
        if fuzzy:
            fuzzy.sort(key=len)               # 最短的通常是主版本,而非 -preview-0x 变体
            print(f"    模型『{preferred}』模糊匹配到: {fuzzy[0]}")
            return fuzzy[0]
        print(f"    ⚠ 模型『{preferred}』不在该站列表中,自动改选其他模型")
    for kw in ("claude-sonnet", "claude", "gemini", "gpt-4o", "gpt-4", "deepseek", "qwen"):
        hit = next((i for i in ids if i and kw in i.lower()), None)
        if hit:
            return hit
    return ids[0] if ids else preferred or "gpt-4o"


def run_problem(tag: str, text: str):
    from app.ir import ProofGraph
    from app.pipeline import run_pipeline, synthesize_solution

    print(f"\n—— 题目[{tag}]:{text}")
    t0 = time.time()
    graph_dict = validation = mode = None
    err = None
    for ev in run_pipeline(text):
        if ev["type"] == "tool_result":
            print(f"    · {ev['name']}: {ev.get('summary', '')}")
        elif ev["type"] == "pipeline_error":
            err = f"{ev['stage']}: {ev['message']}"
        elif ev["type"] == "pipeline_done":
            graph_dict, validation, mode = ev["graph"], ev["validation"], ev["mode"]
    check(f"[{tag}] 结构图生成", graph_dict is not None, err or "")
    if graph_dict is None:
        return
    check(f"[{tag}] 走真实 LLM(非 demo)", mode in ("llm", "llm_open"), f"mode={mode}")
    check(f"[{tag}] 结构校验通过", bool(validation and validation.get("ok")),
          json.dumps((validation or {}).get("errors", [])[:2], ensure_ascii=False))
    n_nodes = len(graph_dict["nodes"])
    check(f"[{tag}] 主图粒度合理(4~30 节点)", 4 <= n_nodes <= 30, f"{n_nodes} 节点")

    graph = ProofGraph.from_dict(graph_dict)
    solution = "".join(synthesize_solution(graph))
    check(f"[{tag}] 据结构图重建完整解答", len(solution) > 200, f"仅 {len(solution)} 字")
    check(f"[{tag}] 解答有结论收尾", ("∎" in solution) or ("证毕" in solution)
          or ("得证" in solution) or ("综上" in solution), solution[-80:])
    titles = [n.get("title", "") for n in graph_dict["nodes"] if n.get("node_type") != "goal"]
    covered = sum(1 for t in titles if t and t[:6] in solution)
    check(f"[{tag}] 解答覆盖结构图步骤(≥60%)",
          covered >= max(1, int(len(titles) * 0.6)), f"{covered}/{len(titles)}")
    print(f"    用时 {time.time() - t0:.1f}s,解答 {len(solution)} 字")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", action="append", default=None,
                    help="候选 base_url,可多次给出或用逗号分隔;逐个尝试直到成功")
    ap.add_argument("--key", default=os.environ.get("LLM_API_KEY", ""))
    ap.add_argument("--model", default=os.environ.get("LLM_MODEL", ""))
    ap.add_argument("--problem", action="append", default=[],
                    help="追加任意验收题(可多次)")
    args = ap.parse_args()

    # --base 支持多次给出与逗号分隔
    bases = [b.strip() for raw in (args.base or [os.environ.get("LLM_BASE_URL", "")])
             for b in raw.split(",") if b.strip()]
    # 未显式给出时,回退到 var/settings.json 已保存的配置
    if not (bases and args.key):
        from app.core.config import get_settings
        s = get_settings()
        bases = bases or ([s["llm_base_url"]] if s.get("llm_base_url") else [])
        args.key = args.key or s.get("llm_api_key", "")
        args.model = args.model or s.get("llm_model_heavy", "")
    if not (bases and args.key):
        print("缺少 base_url / api_key:用 --base/--key 或环境变量 LLM_BASE_URL/LLM_API_KEY 提供")
        return 2
    if not args.key.isascii() or " " in args.key.strip():
        print(f"api_key 看起来不合法:{args.key[:20]!r}…")
        print("提示:把命令里的占位符替换成真实 key(以 sk- 开头的一长串字符)。")
        return 2

    print(f"[1] 端点探测({len(bases)} 个候选)")
    base, ids = "", []
    for cand in bases:
        print(f"  → 尝试 {cand}")
        base, ids = probe_models(cand, args.key)
        if base:
            break
    check("/models 可达", bool(base), "所有候选端点都失败")
    if not base:
        print("\n端点不可达,请检查 base_url、key 或网络。")
        return 1
    if ids:
        gemini_like = [i for i in ids if i and "gemini" in i.lower()]
        sample = (gemini_like or ids)[:15]
        print(f"    可用模型 {len(ids)} 个,示例: {sample}")
    model = pick_model(ids, args.model)
    print(f"    base={base}  选用模型: {model}")

    # 配置写入 var/settings.json(gitignore 内,不会进仓库);Web UI 即时生效
    from app.core.config import update_settings
    update_settings({"llm_base_url": base, "llm_api_key": args.key,
                     "llm_model_heavy": model, "llm_model_light": model})
    print("    配置已写入 var/settings.json(Web UI 共用,不会提交进仓库)")

    print("[2] 最小对话 ping")
    from app.core.llm_client import LLMClient
    try:
        reply = LLMClient().chat([{"role": "user", "content": "只回复两个字:正常"}],
                                 max_tokens=20)
        check("chat 往返", bool(reply.strip()), repr(reply[:50]))
        print(f"    回复: {reply.strip()[:40]!r}")
    except RuntimeError as e:
        check("chat 往返", False, str(e)[:160])
        return 1

    print("[3] 端到端:结构图 → 校验 → 重建解答")
    for tag, text in PROBLEMS + [(f"custom{i+1}", p) for i, p in enumerate(args.problem)]:
        run_problem(tag, text)

    print(f"\n{'=' * 46}\n通过 {len(PASSED)} / {len(PASSED) + len(FAILED)}"
          + (f"  失败: {FAILED}" if FAILED else "  全部通过 ✓"))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
