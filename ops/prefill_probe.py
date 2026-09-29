#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Холодный prefill-зонд #39333: тот же 361877-токенный промпт, что и niahs-калибровка,
один запрос max_tokens=1; печатает wall и серверные ReqTimeStats-секунды префила."""
import importlib.util, json, random, re, subprocess, sys, time
spec = importlib.util.spec_from_file_location("ab", "/home/molochko/penny/ops/ab_compare.py")
ab = importlib.util.module_from_spec(spec); spec.loader.exec_module(ab)

rnd = random.Random(42)
hay = [ab.sentence(rnd) for _ in range(25000)]
sents = list(hay)
sents[min(len(sents)-1, int(len(sents)*0.03))] = "Alarm bell 101 rings loudly over the rack 101."
ctx = "\n".join(sents)

t0 = time.time()
txt, usage, dt = ab.chat([{"role": "user", "content": ctx}], max_tokens=1, timeout=900)
print(json.dumps({"probe": "cold-prefill", "wall_s": round(dt,2),
                  "prompt_tokens": usage.get("prompt_tokens"),
                  "tok_s_prefill": round((usage.get("prompt_tokens") or 0)/dt,1)}))
