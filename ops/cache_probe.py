#!/usr/bin/env python3
"""cache_probe.py — разделает «рвётся префикс» и «тесно в кеше».

Три прогона одного и того же длинного префикса к локальному SGLang:
  A  cold      — первый запрос, кеш пустой
  B  warm      — префикс идентичен A: должен отдать ~весь prompt как cached
  C  headchurn — идентичен B, НО первая строка заменена (имитация per-request
                 hash/billing-заголовка в начале system-промпта)

Если B кэшируется, а C — нет, то низкий hit-rate на агентском трафике чинится
настройкой клиента (CLAUDE_CODE_ATTRIBUTION_HEADER=0), а не сервером.

Запускать на хосте: python3 ~/agent-tools/cache_probe.py [tokens]
"""
import json
import ssl
import sys
import time
import urllib.request

BASE = "https://127.0.0.1:1234"
CTX = ssl._create_unverified_context()
MODEL = "qwen3.8-flash-next"
TARGET_TOKENS = int(sys.argv[1]) if len(sys.argv) > 1 else 6000

# ~4 символа на токен; наполнение разное на каждой строке, чтобы промпт не
# сжимался и не совпадал с чужими префиксами в radix-дереве
N_LINES = max(200, TARGET_TOKENS // 2)  # кириллица плотнее: ~2 символа/токен
filler = "\n".join(
    f"probe line {i:05d} token {i * 7919 % 100000} — заполнитель для замера префиксного кеша"
    for i in range(N_LINES)
)
PREFIX_TAG = "ZZCACHEPROBE20261014"


def call(head: str, label: str) -> dict:
    prompt = f"{head}\n{filler}"
    body = json.dumps(
        {
            "model": MODEL,
            "prompt": prompt,
            "max_tokens": 8,
            "temperature": 0,
            "stream": False,
        }
    ).encode()
    req = urllib.request.Request(
        f"{BASE}/v1/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer probe"},
    )
    t0 = time.time()
    with urllib.request.urlopen(req, context=CTX, timeout=600) as r:
        out = json.loads(r.read())
    dt = time.time() - t0
    u = out.get("usage", {})
    cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens")
    print(
        f"{label:9s} {dt:6.1f}s  prompt={u.get('prompt_tokens')}  "
        f"cached={cached}  "
        f"hit={'n/a' if cached is None else f'{cached / max(1, u.get("prompt_tokens", 1)) * 100:.1f}%'}' "
    )
    return out


if __name__ == "__main__":
    print(f"target ~{TARGET_TOKENS} tok, filler lines={N_LINES}")
    call(f"{PREFIX_TAG} HEAD-STABLE-A", "A cold")
    call(f"{PREFIX_TAG} HEAD-STABLE-A", "B warm")
    call(f"{PREFIX_TAG} HEAD-CHURN-{int(time.time())}", "C headchurn")
    call(f"{PREFIX_TAG} HEAD-STABLE-A", "D warm-again")
