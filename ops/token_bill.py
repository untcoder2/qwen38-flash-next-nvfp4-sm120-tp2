#!/usr/bin/env python3
# Суммарный учёт токенов стенда: 48ч по ReqTimeStats + живые счётчики /metrics.
import re, subprocess, urllib.request

out = subprocess.run(["docker", "logs", "penny", "--since", "48h"],
                     capture_output=True, text=True)
text = out.stdout + out.stderr
pat = re.compile(r"input_len=(\d+), cached_input_len=(\d+), output_len=(\d+)")
pin = pcl = pout = n = 0
for m in pat.finditer(text):
    il, cl, ol = map(int, m.groups())
    pin += il; pcl += cl; pout += ol; n += 1
print(f"requests (48h):        {n:>12,}")
print(f"prompt tokens:         {pin:>12,}   (hit {pcl/pin*100 if pin else 0:.1f}%)")
print(f"  real recompute:      {pin-pcl:>12,}")
print(f"generated tokens:      {pout:>12,}")
print(f"TOTAL processed:       {pin+pout:>12,}")

try:
    body = urllib.request.urlopen("http://127.0.0.1:1234/metrics", timeout=8).read().decode()
    for name in ("sglang:prompt_tokens_total", "sglang:generation_tokens_total"):
        vals = [float(l.split()[-1]) for l in body.splitlines() if l.startswith(name)]
        print(f"{name} = {sum(vals):,.0f} (с запуска контейнера)")
except Exception as e:
    print("metrics err:", e)
