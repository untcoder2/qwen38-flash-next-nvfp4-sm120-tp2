
import json, ssl, time, urllib.request, os, re
G = "https://127.0.0.1:1234/generate"
_CTX = ssl.create_default_context(cafile="/home/molochko/penny/tls/ca.pem")
def call(payload, timeout=600):
    req = urllib.request.Request(G, data=json.dumps(payload).encode(), headers={"Content-Type":"application/json"})
    t0 = time.time()
    d = json.loads(urllib.request.urlopen(req, timeout=timeout, context=_CTX).read().decode())
    return d, time.time() - t0

# 0) одиночный декод (холостой ход)
d, el = call({"text":"Счёт:", "sampling_params":{"max_new_tokens":800,"temperature":0,"ignore_eos":True}})
u = d["meta_info"]
print("decode-idle: %d токенов за %.2fs = %.1f tok/s" % (u["completion_tokens"], el, u["completion_tokens"]/el))

# 1) уникальный длинный промпт (~100K токенов) — холодный префил
import random
salt = str(random.randint(0, 10**12))
sent = "В протоколе экспедиции «%s» зафиксирован пункт о состоянии шлюза, дубликатах каталога и порядке повторной сверки сегментов кэша. " % salt
long_prompt = sent * 3200   # ~3600*3200 симв -> порядка 100K+ токенов
d1, t1 = call({"text": long_prompt + "\nОтветь одним словом: ок.", "sampling_params":{"max_new_tokens":16,"temperature":0}}, 900)
m1 = d1["meta_info"]
print("cold-prefill: prompt=%d tok, wall=%.2fs, cached=%s" % (m1["prompt_tokens"], t1, m1.get("cached_tokens")))

# 2) точно тот же промпт повторно — тёплый заход (radix/hicache)
d2, t2 = call({"text": long_prompt + "\nОтветь одним словом: ок.", "sampling_params":{"max_new_tokens":16,"temperature":0}}, 900)
m2 = d2["meta_info"]
print("warm-retry  : prompt=%d tok, wall=%.2fs, cached=%s (%.1f%%)" % (m2["prompt_tokens"], t2, m2.get("cached_tokens"), (m2.get("cached_tokens") or 0)/max(m2["prompt_tokens"],1)*100))
print("cold/warm ускорение: %.1fx" % (t1/max(t2,0.01)))
