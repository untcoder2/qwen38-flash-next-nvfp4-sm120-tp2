#!/usr/bin/env python3
"""Батарея 20 задач для GLM-шедоу. База GLM-era: на этом стеке thinking-off
невыразим (в шаблоне чекпоинта нет enable_thinking), kwargs наследуются от
сервера (reasoning_effort=max по умолчанию), max_tokens 400 как у _quality_tls.
К Pennyroyal-ным 20/20 по оси «thinking» НЕ сопоставимо — сравнивать только
счёт 20/20 как порог. Выход: 0 = 20/20, 1 = недобор, 2 = транспорт/конфиг."""
import json, os, ssl, sys, time, urllib.request, urllib.error

HOME = os.environ.get("HOME", "/home/molochko")
CA = os.environ.get("GLM_CA", f"{HOME}/penny/tls/ca.pem")
URL = os.environ.get("GLM_URL", "https://127.0.0.1:1234/v1/chat/completions")
KEY = os.environ.get("GLM_KEY", "sk-local")
MODEL = os.environ.get("GLM_MODEL", "glm-5.3-flash")
TASKS = os.environ.get("GLM_TASKS", f"{HOME}/vllm/_tasks.json")

try:
    ctx = ssl.create_default_context(cafile=CA)
    with open(TASKS, encoding="utf-8") as fh:
        tasks = json.load(fh)
except (OSError, ValueError) as e:
    sys.stderr.write(f"конфиг: {e}\n"); sys.exit(2)
if not tasks:
    sys.stderr.write("пустой список задач — не 20/20, а ноль\n"); sys.exit(2)

ok, fails = 0, []
for i, (q, a) in enumerate(tasks, 1):
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": q}],
        "temperature": 0, "max_tokens": 400,
    }).encode()
    req = urllib.request.Request(URL, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {KEY}",
    })
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
            r = json.loads(resp.read())
        got = (r["choices"][0]["message"].get("content") or "").strip()
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"HTTP {e.code} на задаче {i}: {e.read()[:200]!r}\n"); sys.exit(2)
    except (urllib.error.URLError, TimeoutError, OSError,
            ValueError, KeyError, IndexError) as e:
        sys.stderr.write(f"транспорт/ответ на задаче {i}: {e!r} — не качество\n"); sys.exit(2)
    good = got == a or got.rstrip(".。") == a
    ok += good
    if not good:
        fails.append((i, q[:48], a, got[:64]))
    print(f"{i:02d} {'ok ' if good else 'FAIL'} {time.time()-t0:4.1f}s {q[:56]}")
print(f"\nQUALITY {ok}/{len(tasks)}")
for f in fails:
    print("  ", f)
sys.exit(0 if ok == len(tasks) else 1)
