#!/usr/bin/env python3
"""cache_demo_messages.py — A/B: один и тот же /v1/messages по HTTPS дважды.

Демонстрирует разрыв «клиент видит / сервер делает»: usage в ответе не содержит
cache_read_input_tokens (enable_cache_report=false), при этом сервер реально
переиспользует префикс (видно по времени hot vs cold и по admission-логам).

python3 -I ~/agent-tools/cache_demo_messages.py
"""
import json
import ssl
import time
import urllib.request

CTX = ssl._create_unverified_context()
BIG = "\n".join(
    f"строка {i:04d} значение {i * 104729 % 999983} для теста кеша через messages-API"
    for i in range(2200)
)


def call(tag):
    body = {
        "model": "claude-sonnet-4-5",
        "max_tokens": 8,
        "temperature": 0,
        "system": [
            {"type": "text", "text": "Тестовый системный префикс."},
            {"type": "text", "text": BIG, "cache_control": {"type": "ephemeral"}},
        ],
        "messages": [{"role": "user", "content": "Ответь одним словом."}],
    }
    req = urllib.request.Request(
        "https://127.0.0.1:1234/v1/messages",
        data=json.dumps(body).encode(),
        headers={
            "content-type": "application/json",
            "authorization": "Bearer test",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "prompt-caching-2024-07-31",
        },
    )
    t0 = time.time()
    with urllib.request.urlopen(req, context=CTX, timeout=300) as r:
        out = json.loads(r.read())
    usage = json.dumps(out.get("usage", {}), ensure_ascii=False)
    print(f"{tag}: {time.time() - t0:6.1f}s  usage={usage}")


call("1 cold ")
time.sleep(2)
call("2 warm")
