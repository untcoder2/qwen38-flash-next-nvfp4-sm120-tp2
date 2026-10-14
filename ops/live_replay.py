#!/usr/bin/env python3
"""live_replay.py — проигрывает дамп БОЕВОГО запроса и бисектит, где теряется кеш.

Тело берётся из ~/agent-tools/last_body.json (его пишет cache_trace.py при TRACE_DUMP=1).
Всё, что меняется между прогонами, — только длина истории (берём первые k сообщений и
дописываем один свежий user, чтобы запрос остался валидным). Префиксы живой истории уже
посчитаны сервером на предыдущих ходах, так что ответы должны приходить быстро; медленный
ответ = на этом отрезке рендер не совпадает с тем, что лежит в дереве.

  all      — тело как есть (повтор боевого запроса)
  k=NN     — первые NN сообщений + один свежий user
  ids      — как есть, но из tool_use/tool_result вычищены id (проверка гипотезы про id)
  sysnew   — как есть, но текст system заменён на другой

python3 -I ~/agent-tools/live_replay.py [k ...]
Потом: docker logs --since 2m penny | grep -oE '#cached-token: [0-9]+' | head -N
"""
import copy
import json
import ssl
import sys
import time
import urllib.request

BASE = "https://127.0.0.1:1234"
CTX = ssl._create_unverified_context()
HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))
BODY = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].isdigit() else __import__("os").path.join(HERE, "last_body.json")
KS = [int(a) for a in sys.argv[1:] if a.isdigit()] or [20, 60, 110, 150, 180]

with open(BODY, encoding="utf-8") as f:
    RAW = json.load(f)
print(f"тело: {len(json.dumps(RAW)):,} байт, сообщений {len(RAW.get('messages', []))}, tools {len(RAW.get('tools', []))}\n")
FRESH = {"role": "user", "content": [{"type": "text", "text": "продолжим"}]}


def send(payload, label):
    req = urllib.request.Request(
        BASE + "/v1/messages",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer replay",
                 "anthropic-version": "2023-06-01"},
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=900) as r:
            out = json.loads(r.read())
        dt = time.time() - t0
        u = out.get("usage", {})
        print(f"  {label:12s} {dt:7.2f} с  in={u.get('input_tokens')}")
    except Exception as e:
        print(f"  {label:12s} ошибка: {str(e)[:110]}")
    time.sleep(0.7)


def trunc(k):
    p = copy.deepcopy(RAW)
    p["messages"] = p["messages"][:k] + [FRESH]
    return p


def strip_ids():
    p = copy.deepcopy(RAW)
    for m in p["messages"]:
        c = m.get("content")
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict):
                    b.pop("id", None)
                    b.pop("tool_use_id", None)
    return p


def sysnew():
    p = copy.deepcopy(RAW)
    p["system"] = [{"type": "text", "text": "Совсем другой системный промпт для проверки."}]
    return p


if __name__ == "__main__":
    print("порядок: от самого глубокого префикса к мелким, чтобы не греть дерево зря")
    send(RAW, "all")
    for k in sorted(KS, reverse=True):
        if k < len(RAW["messages"]):
            send(trunc(k), f"k={k}")
    send(strip_ids(), "ids")
    print("\nтеперь cached каждого прогона: docker logs --since 2m penny | grep '#cached-token'")
