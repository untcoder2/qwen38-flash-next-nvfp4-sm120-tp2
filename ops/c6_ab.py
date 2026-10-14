
import json, ssl, time, urllib.request, statistics, threading
G = "https://127.0.0.1:1234/generate"
_CTX = ssl.create_default_context(cafile="/home/molochko/penny/tls/ca.pem")
def lane(out, i):
    text = ("В протоколе экспедиции «л%d» зафиксированы состояние шлюза и порядок сверки. " % i) * 24
    text += "Продолжи нумерацию пунктов с 501 по 600, по одному числу в строке."
    req = urllib.request.Request(G, data=json.dumps({"text": text, "sampling_params":
        {"max_new_tokens": 400, "temperature": 0}}).encode(), headers={"Content-Type":"application/json"})
    t0 = time.time()
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=240, context=_CTX).read().decode())
        out[i] = (d["meta_info"]["completion_tokens"], time.time() - t0)
    except Exception as ex:
        out[i] = (0, time.time() - t0)
for N in (6, 8):
    out = {}
    ts = [threading.Thread(target=lane, args=(out, i)) for i in range(N)]
    t0 = time.time(); [t.start() for t in ts]; [t.join() for t in ts]
    el = time.time() - t0
    tok = sum(v[0] for v in out.values()); per = [v[0]/v[1] for v in out.values() if v[1] > 0]
    print("L=%d: wall=%.1fs | суммарно %d tok = %.1f tok/s | на лан: медиана %.1f, мин %.1f" %
          (N, el, tok, tok/el, statistics.median(per), min(per)))
