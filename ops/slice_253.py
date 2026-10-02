#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Срез v2.5.3 для публикации: скорость, кэш, префил-профиль, accept/retract, NIAHS.
Пишет JSON на хост + печатает сводку. Только чтение/нагрузка-запросы, без рестартов."""
import json, subprocess, sys, time
sys.path.insert(0, "/home/molochko/penny/ops")
out = {"version": "v2.5.3", "ts": time.strftime("%F %T"), "notes": []}

# 1) шильдик + аптайм
d = subprocess.run(["docker", "inspect", "penny", "--format", "{{.Config.Image}} {{.State.StartedAt}}"], capture_output=True, text=True).stdout.strip()
out["image"] = d
r = subprocess.run(["curl", "-s", "-m", "8", "http://127.0.0.1:1234/get_server_info"], capture_output=True, text=True)
try:
    si = json.loads(r.stdout)
    out["server"] = {k: si.get(k) for k in ("mem_fraction_static", "context_length", "max_total_num_tokens", "enable_hierarchical_cache", "speculative_algorithm", "speculative_num_steps", "speculative_eagle_topk", "speculative_num_draft_tokens")}
except Exception as e:
    out["notes"].append(f"server_info: {e}")

# 2) greedy + warm/cold prefill (переиспользуем штатные скрипты, парсим хвосты)
def run_tail(cmd, n=6):
    p = subprocess.run(cmd, capture_output=True, text=True, cwd="/home/molochko/penny/ops")
    return (p.stdout + p.stderr).splitlines()[-n:]
out["short_bench"] = run_tail(["python3", "short_bench.py"], 2)
out["ctx_speed"] = run_tail(["python3", "ctx_speed.py"], 4)

# 3) verify против 253-live
import glob
man = glob.glob("/home/molochko/penny/snapshots/*253-live/MANIFEST.json")
out["verify"] = run_tail(["python3", "verify.py", man[0]], 8) if man else ["NO 253-live MANIFEST"]

# 4) холодный префил-зонд на 362K (как в A/B) + NIAHS целиком
out["niahs"] = run_tail(["python3", "ab_compare.py", "--tag", "canon253", "--out", "ab-canon253.json", "--only", "niahs"], 7)
try:
    out["niahs_json"] = json.load(open("/home/molochko/penny/ops/ab-canon253.json"))["niahs"]
except Exception as e:
    out["notes"].append(f"niahs json: {e}")

# 5) token-билл за время жизни контейнера
import urllib.request, re as _re
try:
    body = urllib.request.urlopen("http://127.0.0.1:1234/metrics", timeout=8).read().decode()
    acc = {}
    for nm in ("sglang:prompt_tokens_total", "sglang:generation_tokens_total"):
        acc[nm] = sum(float(l.split()[-1]) for l in body.splitlines() if l.startswith(nm))
    out["counters_since_boot"] = {k.split(":")[1]: int(v) for k, v in acc.items()}
except Exception as e:
    out["notes"].append(f"metrics: {e}")

json.dump(out, open("/home/molochko/penny/ops/slice-253.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False, indent=1))
