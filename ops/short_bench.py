
import json, ssl, statistics, time, urllib.request
G = "https://127.0.0.1:1234/generate"
_CTX = ssl.create_default_context(cafile="/home/molochko/penny/tls/ca.pem")
filler = ("В отчёте станции наблюдения описаны состояние антенны, калибровка приёмника, "
          "проверка эталонного генератора и журналы сверки частот. " * 60)
base = filler + ("\nПродолжи список: раз, два, три, четыре... до ста, каждой цифрой с новой строки, без пояснений.")
def one():
    req = urllib.request.Request(G, data=json.dumps({"text": base, "sampling_params":
        {"max_new_tokens": 512, "temperature": 0}}).encode(),
        headers={"Content-Type":"application/json"})
    t0 = time.time()
    d = json.loads(urllib.request.urlopen(req, timeout=300, context=_CTX).read().decode())
    el = time.time() - t0
    m = d["meta_info"]
    ct = m["completion_tokens"]
    return ct/el, ct, m.get("e2e_latency") or el
# прогрев + 5 замеров
one()
res = [one() for _ in range(5)]
tps = [r[0] for r in res]
print("short-context greedy OSL512: ", " | ".join("%.1f" % x for x in tps))
print("median=%.1f tok/s  min=%.1f  max=%.1f" % (statistics.median(tps), min(tps), max(tps)))
# chat-вариант тоже (с reasoning, как реальная агентская нагрузка, но короткий)
C = "https://127.0.0.1:1234/v1/chat/completions"
req = urllib.request.Request(C, data=json.dumps({"model":"qwen3.8-flash-next","messages":[{"role":"user","content":"Перечисли числа 1-300 по одному в строке."}],"reasoning_effort":"low","max_tokens":900,"temperature":0}).encode(), headers={"Content-Type":"application/json"})
t0=time.time(); d=json.loads(urllib.request.urlopen(req, timeout=300, context=_CTX).read().decode()); el=time.time()-t0
u=d["usage"]; print("chat low-effort: %d токенов за %.2fs = %.1f tok/s (reasoning=%d)" % (u["completion_tokens"], el, u["completion_tokens"]/el, u.get("reasoning_tokens") or 0))
