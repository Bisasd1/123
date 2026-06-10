import requests

KEY = "sk-1vH9P4gY8vpU6dHQpOgYy3UKeYdOdwdBoikiKu3qy1cG01JR"
BASES = [
    "https://api.ttk.homes/v1",
    "https://ai.ttk.homes/v1",
    "https://api-cn.ttk.homes:3333/v1",
    "https://ai.ttk.homes",
]
H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}

for base in BASES:
    try:
        r = requests.get(base.rstrip("/") + "/models", headers=H, timeout=15)
        ids = [m.get("id") for m in r.json().get("data", [])]
        print(f"[{base}] models {r.status_code}: {len(ids)} 个, 示例: {ids[:8]}")
    except Exception as e:
        print(f"[{base}] models FAILED: {type(e).__name__}: {str(e)[:120]}")
        continue
    try:
        model = ids[0] if ids else "gpt-4o"
        r2 = requests.post(base.rstrip("/") + "/chat/completions", headers=H,
                           json={"model": model,
                                 "messages": [{"role": "user", "content": "只回复两个字:正常"}],
                                 "max_tokens": 100}, timeout=60)
        d = r2.json()
        content = d.get("choices", [{}])[0].get("message", {}).get("content", "")
        print(f"   chat({model}) {r2.status_code}: {content[:60]!r} fp={d.get('system_fingerprint')}")
    except Exception as e:
        print(f"   chat FAILED: {type(e).__name__}: {str(e)[:120]}")
