#!/usr/bin/env python3
"""cache_window.py — hit-rate за короткое окно, а не «с момента рестарта».

Дашборд и sglang-счётчики дают кумулятив с подъёма контейнера — он тяжёлый и
враньё про «сейчас» не рассказывает. Тут: снимок /metrics, пауза, второй снимок,
дельта. Хит окна = (device+host прирост) / (прирост prompt-токенов).

Запуск на хосте: python3 -I ~/agent-tools/cache_window.py [секунд]
"""
import json
import ssl
import sys
import time
import urllib.request

BASE = "https://127.0.0.1:1234"
CTX = ssl._create_unverified_context()
SECS = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0

NEED = {
    "prompt": r'sglang:prompt_tokens_total{.*} ([\d.e+]+)',
    "uncached": r'sglang:uncached_prompt_tokens_histogram_sum{.*} ([\d.e+]+)',
    "dev": r'sglang:cached_tokens_total{cache_source="device".*} ([\d.e+]+)',
    "host": r'sglang:cached_tokens_total{cache_source="host".*} ([\d.e+]+)',
    "evict": r'sglang:evicted_tokens_total{.*} ([\d.e+]+)',
    "run": r'sglang:num_running_reqs{.*} ([\d.e+]+)',
    "usage": r'sglang:full_token_usage{.*} ([\d.e+]+)',
}


def snap():
    import re

    with urllib.request.urlopen(
        urllib.request.Request(f"{BASE}/metrics", headers={"Authorization": "Bearer x"}),
        context=CTX,
        timeout=30,
    ) as r:
        txt = r.read().decode()
    out = {}
    for k, pat in NEED.items():
        vals = [float(m) for m in re.findall(pat, txt)]
        out[k] = sum(vals) if k in ("prompt", "uncached", "dev", "host", "evict") else (
            max(vals) if vals else 0.0
        )
    return out


a = snap()
t0 = time.time()
time.sleep(SECS)
b = snap()
dt = time.time() - t0

d_prompt = b["prompt"] - a["prompt"]
d_unc = b["uncached"] - a["uncached"]
d_hit = (b["dev"] - a["dev"]) + (b["host"] - a["host"])
d_ev = b["evict"] - a["evict"]

print(f"окно {dt:.0f} с:")
print(f"  запросов prompt-токенов: {d_prompt:,.0f}")
print(f"  попало в кеш          : {d_hit:,.0f}  (device {b['dev']-a['dev']:,.0f} / host {b['host']-a['host']:,.0f})")
print(f"  мимо кеша (ре-префилл): {d_unc:,.0f}")
print(f"  вытеснено из KV        : {d_ev:,.0f}")
print(f"  running reqs сейчас    : {b['run']:.0f}   KV заполнен: {b['usage']*100:.0f}%")
if d_prompt > 0:
    print(f"\n  ХИТ ЗА ОКНО = {d_hit / d_prompt * 100:.1f}%   (кумулятив с рестарта = {(1 - b['uncached'] / b['prompt']) * 100:.1f}%)")
else:
    print("\n  за окно prompt-токенов нет — окно пустое, увеличь секунды")
