#!/usr/bin/env python3
"""path_ab.py — A/B глубины префиксного кеша: /v1/chat/completions vs /v1/messages.

Один и тот же сервер, одни и те же флаги и TLS. На каждом пути два запроса:
  #1 — большой СТАБИЛЬНЫЙ префикс в первом сообщении (греет кеш)
  #2 — тот же префикс + дописанные assistant/user следом (как живой ход агента)
Если на #2 сервер переиспользует весь префикс — путь prefix-stable; если только
голову — префикс рвётся на рендере этого эндпоинта, и клиент тут ни при чём.

Сам cached_tokens сервер не отдаёт (enable_cache_report=false), поэтому меришь НЕ
usage, а время: холодный ~30K-префилл = единицы секунд, тёплый = ~0.3-0.8 с.
Рядом надо снять лог: docker logs --since 2m penny | grep 'cached-token: [1-9]'.

python3 -I ~/agent-tools/path_ab.py [tok]
"""
import json
import ssl
import sys
import time
import urllib.request

BASE = "https://127.0.0.1:1234"
CTX = ssl._create_unverified_context()
MODEL = "qwen3.8-flash-next"
# кириллический наполнитель токенизируется плотнее: ~2 символа на токен
N_LINES = max(50, (int(sys.argv[1]) if len(sys.argv) > 1 else 30000) // 2)
FILLER = "\n".join(
    f"строка {i:05d} значение {i * 7919 % 1000000} — префикс для A/B двух эндпоинтов"
    for i in range(N_LINES)
)
TAG = "ZZPATHAB"


def post(path, body, label):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer ab"},
    )
    t0 = time.time()
    with urllib.request.urlopen(req, context=CTX, timeout=900) as r:
        r.read()
    print(f"  {label:34s} {time.time() - t0:6.2f} с")


def chat_msg(txt):
    return {"role": "user", "content": txt}


def run(path, mk, label):
    print(f"{path}  ({label})")
    post(path, mk(f"{TAG} {path} call1", []), "#1 холод (греет префикс)")
    time.sleep(1.0)
    post(path, mk(f"{TAG} {path} call1", ["ok", "ещё вопрос"]), "#2 тёпл (тот же префикс + дописок)")


def mk_chat(head, extra):
    msgs = [chat_msg(head + "\n" + FILLER)]
    for i, e in enumerate(extra):
        msgs.append({"role": "assistant" if i % 2 == 0 else "user", "content": e})
    return {"model": MODEL, "messages": msgs, "max_tokens": 8, "temperature": 0}


def mk_anth(head, extra):
    msgs = [{"role": "user", "content": [{"type": "text", "text": head + "\n" + FILLER}]}]
    for i, e in enumerate(extra):
        msgs.append({"role": "assistant" if i % 2 == 0 else "user", "content": [{"type": "text", "text": e}]})
    return {
        "model": "claude-opus-5-5",
        "system": [{"type": "text", "text": "Ты помощник. Отвечай коротко."}],
        "messages": msgs,
        "max_tokens": 8,
    }


if __name__ == "__main__":
    print(f"префикс ~{N_LINES * 2:,} символов (~{(N_LINES * 2) // 2:,} токенов)\n")
    run("/v1/chat/completions", mk_chat, "Kimi-путь")
    print()
    run("/v1/messages", mk_anth, "Claude Code-путь")
    print("\nсравни время #2 с #1: если #2 на порядок быстрее — префикс переиспользуется целиком")
