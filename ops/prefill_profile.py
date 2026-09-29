#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Префил/декод профиль агентов по docker-логам стенда (последние 48ч)."""
import re, subprocess, statistics as st
from collections import defaultdict

_r = subprocess.run(["docker", "logs", "penny", "--since", "48h"], capture_output=True, text=True)
out = _r.stdout + _r.stderr
RT = re.compile(r"ReqTimeStats\(rid=\w+, input_len=(\d+), cached_input_len=(\d+), output_len=(\d+), attempts=(\d+), type=(\w+)\): queue_duration=([\d.]+)ms, initial_prefill_elapsed=([\d.]+)ms, post_prefill_elapsed=([\d.]+)ms, forward_duration=([\d.]+)ms")
PB = re.compile(r"Prefill batch.*input throughput \(token/s\): ([\d.]+)")
DB = re.compile(r"#running-req: (\d+)")
IDLE = re.compile(r"sleep_on_idle|Idle")

buckets = defaultdict(list)
def b(n):
    for lim, name in ((8192, "0-8K"), (65536, "8-64K"), (262144, "64-256K"), (655360, "256-655K"), (10**9, ">655K")):
        if n < lim:
            return name

pb_vals = [float(m[0]) for m in PB.findall(out)]
running = [int(m[0]) for m in DB.findall(out)]
nreq = 0
for m in RT.finditer(out):
    il, cil, ol, at, typ, q, ip, pp, fd = m.groups()
    il, cil, ol = int(il), int(cil), int(ol)
    q, ip, pp, fd = map(float, (q, ip, pp, fd))
    nreq += 1
    r = buckets[b(il)]
    cold = il > 0 and cil / il < 0.5 and ip > 20
    prefill_tps = (il / (ip / 1000)) if cold else None
    hit = cil / il * 100 if il else 100
    decode_tps = ol / (pp / 1000) if pp > 0 and ol else None
    r.append((prefill_tps, hit, decode_tps, q, ol))

print(f"ReqTimeStats-запросов: {nreq} | Prefill batch-строк: {len(pb_vals)} | Decode sample: {len(running)}")
if pb_vals:
    print(f"Сквозной prefill throughput (лог scheduler'а): p50={st.median(pb_vals):,.0f} p90={sorted(pb_vals)[int(len(pb_vals)*.9)]:,.0f} max={max(pb_vals):,.0f} tok/s")
if running:
    hist = {k: running.count(k) for k in sorted(set(running))}
    print("Популация running-req (лан) по decode-сэмплам:", hist)
print(f"\n{'bucket':10s} {'n':>5s} {'prefill tok/s':>14s} {'hit %':>7s} {'decode tok/s':>13s} {'queue ms':>9s} {'out med':>8s}")
for name in ("0-8K", "8-64K", "64-256K", "256-655K", ">655K"):
    rows = buckets.get(name)
    if not rows:
        print(f"{name:10s} {0:>5d} —"); continue
    pf = [r[0] for r in rows if r[0]]
    ht = [r[1] for r in rows]
    dc = [r[2] for r in rows if r[2]]
    q  = [r[3] for r in rows]
    ol = [r[4] for r in rows]
    print(f"{name:10s} {len(rows):>5d} {st.median(pf):>14,.0f} {st.median(ht):>6.1f}% "
          f"{(st.median(dc) if dc else float("nan")):>13,.0f} {st.median(q):>9.1f} {st.median(ol):>8.0f}")
