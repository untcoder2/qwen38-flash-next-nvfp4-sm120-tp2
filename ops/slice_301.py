#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Срез v3.0.1: скорость, кэш, префил, accept/retract, NIAHS + спекулятивный профиль
adaptive. Только чтение/нагрузка-запросы, без рестартов. Сравнивает с базами v2.5.3:
pool 2752640, tok/s med 193.8, accept bs1 3.15 / bs8 2.96 / long 2.36."""
import json, os, subprocess, sys, time
sys.path.insert(0, "/home/molochko/penny/ops")
out = {"version": "v3.0.1", "ts": time.strftime("%F %T"), "notes": [],
       "baseline_253": {"pool": 2752640, "toks_med": 193.8,
                        "accept_bs1": 3.153, "accept_bs8": 2.964, "accept_long": 2.364}}

d = subprocess.run(["docker", "inspect", "penny", "--format", "{{.Config.Image}} {{.State.StartedAt}}"], capture_output=True, text=True).stdout.strip()
out["image"] = d
r = subprocess.run(["curl", "-s", "-m", "8", "--cacert", os.environ.get("SLICE_CA", "/home/molochko/penny/tls/ca.pem"),
                  "https://127.0.0.1:1234/get_server_info"], capture_output=True, text=True)
try:
    si = json.loads(r.stdout)
    keep = {k: v for k, v in si.items() if any(s in k.lower() for s in
            ("speculative", "adaptive", "draft"))}
    out["server"] = {k: si.get(k) for k in ("mem_fraction_static", "context_length",
                    "max_total_num_tokens", "enable_hierarchical_cache", "mamba_track_interval",
                    "max_running_requests")}
    out["spec"] = keep
except Exception as e:
    out["notes"].append(f"server_info: {e}")

def run_tail(cmd, n=6):
    p = subprocess.run(cmd, capture_output=True, text=True, cwd="/home/molochko/penny/ops")
    return (p.stdout + p.stderr).splitlines()[-n:]

# скорость: одиночный стрим + рефил контекста (те же скрипты, что давали 193.8/214)
out["short_bench"] = run_tail(["python3", "short_bench.py"], 2)
out["ctx_speed"] = run_tail(["python3", "ctx_speed.py"], 4)

# accept профиль на новом движке — свежие файлы (бюджет считается на out_file)
for mode, extra in (("bs1", []), ("bs4", []), ("bs8", []), ("long", [])):
    out.setdefault("spec_diag", {})[mode] = run_tail(
        ["python3", "spec_diag.py", "--mode", mode, "--out", "spec_diag_301.jsonl"] + extra, 5)
out["spec_diag_report"] = run_tail(["python3", "spec_diag.py", "--mode", "report",
                                   "--out", "spec_diag_301.jsonl"], 30)

# verify против свежего снапшота 301-live (создаётся до прогона среза)
import glob
man = sorted(glob.glob("/home/molochko/penny/snapshots/*301-live/MANIFEST.json"))
out["verify"] = run_tail(["python3", "verify.py", man[-1]], 8) if man else ["NO 301-live MANIFEST"]

# NIAHS целиком
out["niahs"] = run_tail(["python3", "ab_compare.py", "--tag", "canon301", "--out", "ab-canon301.json", "--only", "niahs"], 7)
try:
    out["niahs_json"] = json.load(open("/home/molochko/penny/ops/ab-canon301.json"))["niahs"]
except Exception as e:
    out["notes"].append(f"niahs json: {e}")

# token-билл с момента рестарта
import ssl, urllib.request
try:
    body = urllib.request.urlopen("https://127.0.0.1:1234/metrics", timeout=8,
                             context=ssl.create_default_context(cafile=os.environ.get("SLICE_CA", "/home/molochko/penny/tls/ca.pem"))).read().decode()
    acc = {}
    for nm in ("sglang:prompt_tokens_total", "sglang:generation_tokens_total"):
        acc[nm] = sum(float(l.split()[-1]) for l in body.splitlines() if l.startswith(nm))
    out["counters_since_boot"] = {k.split(":")[1]: int(v) for k, v in acc.items()}
except Exception as e:
    out["notes"].append(f"metrics: {e}")

json.dump(out, open("/home/molochko/penny/ops/slice-301.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False, indent=1))
