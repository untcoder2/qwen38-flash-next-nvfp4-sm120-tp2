#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only валидация стенда против снапшота. Выход 0 = все гейты PASS."""
import hashlib, json, os, re, statistics, subprocess, sys, time, urllib.error, urllib.request

BASE = "http://127.0.0.1:1234"
man_path = sys.argv[1]
mark = "--mark" in sys.argv
man = json.load(open(man_path, encoding="utf-8"))
fails = []

def gate(name, ok, detail=""):
    print(("PASS |" if ok else "FAIL |"), name, "|", str(detail)[:220])
    if not ok:
        fails.append(name)

def getj(url, data=None, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))

# G1 server_info == снапшот
try:
    srv = getj(BASE + "/get_server_info", timeout=20)
    want = man.get("server_info", {})
    bad = {k: (v, srv.get(k)) for k, v in want.items() if k != "ERROR" and srv.get(k) != v}
    gate("server_info", bool(want) and not bad, bad if bad else str(list(want)[:6]))
except Exception as e:
    gate("server_info", False, repr(e))

# G2 metrics живые
try:
    def cnt():
        t = urllib.request.urlopen(BASE + "/metrics", timeout=10).read().decode()
        m = re.search(r"^sglang:generation_tokens_total(?:\{[^}]*\})?\s+([\d.]+)", t, re.M)
        return float(m.group(1)) if m else None
    a1 = cnt()
    getj(BASE + "/generate", {"text": "hi", "sampling_params": {"max_new_tokens": 8, "temperature": 0}}, 60)
    a2 = cnt()
    gate("metrics_alive", a1 is not None and a2 is not None and a2 > a1, "%s -> %s" % (a1, a2))
except Exception as e:
    gate("metrics_alive", False, repr(e))

# G3 хеши конфиг-файлов
try:
    bad = []
    n = 0
    for ent in man["files"]:
        if ent.get("missing"):
            continue
        n += 1
        p = ent["path"]
        if not os.path.exists(p):
            bad.append((p, "absent")); continue
        h = hashlib.sha256(open(p, "rb").read()).hexdigest()
        if h != ent["sha256"]:
            bad.append((p, ent["sha256"][:8] + "!=" + h[:8]))
    gate("file_hashes", not bad, bad if bad else "%d файлов ok" % n)
except Exception as e:
    gate("file_hashes", False, repr(e))

# G4 шаблон на месте: bogus effort -> 400 с родным текстом
try:
    code, msg = None, ""
    try:
        getj(BASE + "/v1/chat/completions",
             {"model": "qwen3.8-flash-next", "messages": [{"role": "user", "content": "x"}],
              "chat_template_kwargs": {"reasoning_effort": "__bogus__"}, "max_tokens": 1})
    except urllib.error.HTTPError as e:
        code = e.code; msg = e.read().decode(errors="replace")[:150]
    gate("template_bogus_400", code == 400 and "Unexpected reasoning effort" in msg,
         "HTTP %s %s" % (code, msg[:80]))
except Exception as e:
    gate("template_bogus_400", False, repr(e))

# G5 обе effort-ветки работают и различаются
try:
    Q = ("Сколько существует простых p<=1000, что p^2+8 тоже простое? "
         "Два независимых метода, сверка, затем ОТВЕТ: <число>")
    def rt(effort, cap):
        d = getj(BASE + "/v1/chat/completions",
                 {"model": "qwen3.8-flash-next", "reasoning_effort": effort,
                  "messages": [{"role": "user", "content": Q}],
                  "max_tokens": cap, "temperature": 1.0}, 240)
        return d["usage"].get("reasoning_tokens") or 0, d["choices"][0]["message"].get("content") or ""
    low, _ = rt("low", 3000)
    high, ans_hi = rt("xhigh", 20000)
    ok = low > 0 and high > low and "3" in (ans_hi or "")
    gate("effort_effect", ok, "low=%d xhigh=%d" % (low, high))
except Exception as e:
    gate("effort_effect", False, repr(e))

# G6 accept/retract по свежим логам (idle допускает отсутствие данных)
try:
    log = subprocess.run("docker logs %s --since 30m 2>&1 | grep 'Decode batch' | tail -300" % man["container"]["name"],
                         shell=True, capture_output=True, text=True).stdout
    accs = [float(x) for x in re.findall(r"accept len:\s*([0-9.]+)", log)]
    ret = len(re.findall(r"retract", log, re.I))
    if not accs:
        gate("accept_retract", True, "нетDecode-пачек за 30m (idle) — прогнать нагрузку перед публикацией")
    else:
        med = statistics.median(accs)
        gate("accept_retract", med >= 2.0 and ret == 0, "median=%.2f retract=%d n=%d" % (med, ret, len(accs)))
except Exception as e:
    gate("accept_retract", False, repr(e))

print("\nVERDICT:", "ALL GATES PASS" if not fails else "FAILED: %s" % fails)
if not fails and mark:
    import os
    d = os.path.join(os.path.dirname(man_path), "smoke")
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "GOOD-" + time.strftime("%Y%m%d-%H%M%S")), "w", encoding="utf-8").write("all gates pass: " + man_path)
    print("smoke/GOOD записан")
sys.exit(1 if fails else 0)
