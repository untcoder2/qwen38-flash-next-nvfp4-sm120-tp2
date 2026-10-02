#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Токен-билл за N дней по wire-логам всех сессий Kimi Code (main+subagents).
Суммирует usage на step.end: prompt = inputOther+inputCacheRead+inputCacheCreation."""
import glob, json, os, sys, time

HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 96
ROOT = os.path.expanduser("~/.kimi-code/sessions")
cutoff_ms = time.time() * 1000 - HOURS * 3600 * 1000

import subprocess
tot = {"n": 0, "prompt": 0, "cache_read": 0, "gen": 0}
per_ws = {}
for wf in glob.glob(os.path.join(ROOT, "wd_*", "session_*", "agents", "*", "wire.jsonl")):
    ws = wf.split(os.sep)[-5]
    acc = per_ws.setdefault(ws, {"n": 0, "prompt": 0, "gen": 0})
    with open(wf, encoding="utf-8", errors="ignore") as f:
        for line in f:
            if '"usage"' not in line or '"step.end"' not in line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("time", 0) < cutoff_ms:
                continue
            u = (r.get("event") or {}).get("usage") or {}
            if not u:
                continue
            p = u.get("inputOther", 0) + u.get("inputCacheRead", 0) + u.get("inputCacheCreation", 0)
            g = u.get("output", 0)
            tot["n"] += 1; tot["prompt"] += p; tot["gen"] += g; tot["cache_read"] += u.get("inputCacheRead", 0)
            acc["n"] += 1; acc["prompt"] += p; acc["gen"] += g

print(f"=== токен-билл за {HOURS:.0f} ч ===")
print(f"агентских шагов: {tot['n']:,}")
print(f"prompt токенов:  {tot['prompt']:,}  (кэш-хит {tot['cache_read']/max(tot['prompt'],1)*100:.1f}%)")
print(f"generated:       {tot['gen']:,}")
print(f"ВСЕГО:           {tot['prompt']+tot['gen']:,}")
print("\nпо workspace (prompt+gen, шаги):")
for ws, a in sorted(per_ws.items(), key=lambda x: -(x[1]["prompt"] + x[1]["gen"])):
    if a["n"]:
        print(f"  {ws:36s} {a['prompt']+a['gen']:>15,}  ({a['n']:,} шагов)")

# cross-check: живой счётчик текущего контейнера
try:
    import urllib.request
    body = urllib.request.urlopen("http://192.168.2.127:1234/metrics", timeout=8).read().decode()
    acc2 = {}
    for nm in ("sglang:prompt_tokens_total", "sglang:generation_tokens_total"):
        acc2[nm] = sum(float(l.split()[-1]) for l in body.splitlines() if l.startswith(nm))
    print(f"\n[сверка] серверный счётчик с запуска 2.5.3: prompt={acc2['sglang:prompt_tokens_total']:,.0f}, gen={acc2['sglang:generation_tokens_total']:,.0f}")
except Exception as e:
    print("metrics n/a:", e)
