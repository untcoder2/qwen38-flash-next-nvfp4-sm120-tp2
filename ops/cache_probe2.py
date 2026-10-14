#!/usr/bin/env python3
"""cache_probe2.py — чистый тест «дерётся ли префикс, если менять только первую строку».

Маленький промпт (~600 токенов), три запроса с короткими паузами, чтобы префикс
заведомо не успел упариться под давлением вытеснения:
  1 stable   — identical twice
  2 head     — first line changed, rest identical
Считаем hit по usage (OpenAI-compat SGLang кладёт cached_tokens в details).

python3 -I ~/agent-tools/cache_probe2.py [repeats] [gap_sec]
"""
import json
import ssl
import sys
import time
import urllib.request

BASE = "https://127.0.0.1:1234"
CTX = ssl._create_unverified_context()
MODEL = "qwen3.8-flash-next"
REPEATS = int(sys.argv[1]) if len(sys.argv) > 1 else 3
GAP = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0

body_txt = "\n".join(
    f"строка {i:03d} значение {i * 104729 % 999983} для короткого префиксного теста"
    for i in range(60)
)


def hit(head: str):
    prompt = f"{head}\n{body_txt}"
    req = urllib.request.Request(
        f"{BASE}/v1/completions",
        data=json.dumps(
            {"model": MODEL, "prompt": prompt, "max_tokens": 1, "temperature": 0}
        ).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer probe"},
    )
    t0 = time.time()
    with urllib.request.urlopen(req, context=CTX, timeout=300) as r:
        out = json.loads(r.read())
    dt = time.time() - t0
    u = out.get("usage", {})
    pt = u.get("prompt_tokens") or 1
    det = u.get("prompt_tokens_details") or {}
    cached = det.get("cached_tokens")
    print(
        f"  {dt * 1000:6.0f} ms  prompt={pt:6d}  cached={cached}  "
        f"hit={'?' if cached is None else f'{cached / pt * 100:5.1f}%'}"
    )


for n in range(1, REPEATS + 1):
    print(f"round {n}: stable prefix (same first line)")
    hit("PROBE2 HEAD-FIXED")
    time.sleep(GAP)
    print(f"round {n}: head changed (per-request id in line 1)")
    hit(f"PROBE2 HEAD-CHURN {time.time_ns()}")
    time.sleep(GAP)
