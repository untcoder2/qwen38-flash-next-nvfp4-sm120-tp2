# -*- coding: utf-8 -*-
"""burst N потоков на /generate — проверка скорости стенда под параллельными агентами.

С 14.10 инфер только https, лист самоподписанный -> пиним QwenHome-CA (как в виджете).
Нагрузка идёт с Windows по LAN: на стенде ничего не ставим. Помните, что эти потоки
конкурируют с живой генерацией (в т.ч. с вашей текущей сессией) — BURST_N=1 для пробы.

  BURST_N=4 BURST_TOK=5000 BURST_THREADS=4 python ops/widget/_burst4.py
"""
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.request

BASE = os.environ.get("BURST_BASE", "https://192.168.2.127:1234")
CA = os.environ.get("BURST_CA") or os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "certs", "qwen-home-ca.pem"))
N = int(os.environ.get("BURST_TOK", "5000"))       # токенов на поток
THREADS = int(os.environ.get("BURST_THREADS", "4"))

_ctx = None
if BASE.startswith("https"):
    _ctx = ssl.create_default_context()
    if os.path.exists(CA):
        _ctx.load_verify_locations(cafile=CA)

fails = []
gens = []


def agent(i):
    body = {"text": "Перечисли числа с %d по %d по одному в строке, ничего лишнего." % (i * N + 1, (i + 1) * N),
            "sampling_params": {"max_new_tokens": N, "temperature": 0}}
    req = urllib.request.Request(BASE + "/generate",
                                 data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    kw = {"timeout": 600}
    if _ctx is not None:
        kw["context"] = _ctx
    try:
        raw = urllib.request.urlopen(req, **kw).read()
        try:
            gens.append(json.loads(raw).get("meta_info", {}).get("completion_tokens", 0))
        except Exception:
            gens.append(0)
    except Exception as e:
        fails.append("поток %d: %s %s" % (i, type(e).__name__, str(e)[:120]))


t0 = time.time()
ths = [threading.Thread(target=agent, args=(i,)) for i in range(THREADS)]
[t.start() for t in ths]
[t.join() for t in ths]
el = time.time() - t0

print("CA: %s (%s) | поток=%d токенов просили=%d" % (CA, "есть" if os.path.exists(CA) else "НЕТ", THREADS, N))
print("выпущено: %d токенов за %.1f с = %.1f tok/s суммарно" % (sum(gens), el, sum(gens) / max(el, 0.001)))
for f in fails:
    print("FAIL", f)
print("burst готов" if not fails else "burst С ОШИБКАМИ: %d из %d" % (len(fails), THREADS))
raise SystemExit(1 if fails else 0)
