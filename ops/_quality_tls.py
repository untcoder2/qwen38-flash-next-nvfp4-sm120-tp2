#!/usr/bin/env python3
"""Батарея качества 20/20 поверх TLS (замена fork-era _quality.py).
Берёт ~/vllm/_tasks.json, шлёт temp=0 в /v1/chat/completions с ca.pem.
Порог: 20/20. Выход 0 iff полный проход."""
import json, ssl, sys, time, urllib.request

CA = "/home/molochko/penny/tls/ca.pem"
URL = "https://127.0.0.1:1234/v1/chat/completions"
ctx = ssl.create_default_context(cafile=CA)
tasks = json.load(open("/home/molochko/vllm/_tasks.json"))
ok = 0
fails = []
for i, (q, a) in enumerate(tasks, 1):
    body = json.dumps({
        "model": "qwen3.8-flash-next",
        "messages": [{"role": "user", "content": q}],
        "temperature": 0, "max_tokens": 400,
        "chat_template_kwargs": {"enable_thinking": False},
    }).encode()
    req = urllib.request.Request(URL, data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=120, context=ctx).read())
        got = (r["choices"][0]["message"].get("content") or "").strip()
    except Exception as e:
        got = f"ERROR {e}"
    good = got == a or got.rstrip(".。") == a
    ok += good
    if not good:
        fails.append((i, q[:48], a, got[:64]))
    print(f"{i:02d} {'ok ' if good else 'FAIL'} {time.time()-t0:4.1f}s {q[:56]}")
print(f"\nQUALITY {ok}/{len(tasks)}")
for f in fails:
    print("  ", f)
sys.exit(0 if ok == len(tasks) else 1)
