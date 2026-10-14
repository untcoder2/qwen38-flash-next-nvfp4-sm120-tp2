#!/usr/bin/env python3
"""tail_trim_ab.py — делает ли «подрезка 5-го с конца сообщения» откат кеша до головы.

Клиент (по трейсу) каждый ход снимает ровно 40 символов с хвоста одного СТАРОГО сообщения,
5-го с конца. По JSON это расхождение на ~98% глубины. Сервер же докладывает cached только
~55K из ~130K. Либо рендер поднимает это сообщение сильно раньше, либо plateau серверный.

Опыт: два запроса, отличные ровно на −40 символов в 5-м с конце сообщении. Смотрим cached
второго в server-логе. cached≈весь промпт → подрезка безобидна, ищи серверное. cached≈голове
→ вот он механизм: трогать старое сообщение = пере-префиллить всё после него, как бы глубоко
оно ни сидело.

python3 -I ~/agent-tools/tail_trim_ab.py [tok]
ЗАПУСКАТЬ на хосте; за cached — docker logs --since 2m penny | grep 'cached-token: [1-9]'.
"""
import json
import ssl
import sys
import time
import urllib.request

BASE = "https://127.0.0.1:1234"
CTX = ssl._create_unverified_context()
N_LINES = max(30, (int(sys.argv[1]) if len(sys.argv) > 1 else 20000) // 2)
FILLER = "\n".join(
    f"строка {i:05d} значение {i * 104729 % 999983} — стабильный префикс опыта с подрезкой"
    for i in range(N_LINES)
)
TAG = "ZZTAILTRIM2026"


def txt(s):
    return [{"type": "text", "text": s}]


def build(trim: bool):
    msgs = [{"role": "user", "content": txt(f"{TAG} head\n{FILLER}")}]
    for i in range(8):
        msgs.append({"role": "assistant", "content": txt(f"ответ {i} " + ("x" * 300))})
        msgs.append({"role": "user", "content": txt(f"ход {i} " + ("y" * 300))})
    j = len(msgs) - 5  # то самое 5-е с конца сообщение
    if trim:
        t = msgs[j]["content"][0]["text"]
        msgs[j]["content"][0]["text"] = t[:-40]  # ровно −40 символов, как это делает клиент
    return msgs


def call(msgs, label):
    body = {
        "model": "claude-opus-5-5",
        "system": txt("Ты помощник. Отвечай коротко."),
        "messages": msgs,
        "max_tokens": 8,
    }
    req = urllib.request.Request(
        f"{BASE}/v1/messages",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer probe",
            "anthropic-version": "2023-06-01",
        },
    )
    t0 = time.time()
    with urllib.request.urlopen(req, context=CTX, timeout=900) as r:
        out = json.loads(r.read())
    u = out.get("usage", {})
    print(
        f"  {label:38s} {time.time() - t0:6.2f} с  in={u.get('input_tokens')} "
        f"(сообщений {len(msgs)}, резали индекс {len(msgs) - 5 if label.startswith('B') else '-'})"
    )


print(f"префикс ~{len(FILLER):,} символов")
call(build(False), "A базовый (греет весь промпт)")
time.sleep(1.0)
call(build(True), "B тот же, −40 символов в 5-м с конца")
print("\ncached второго запроса — в server-логе, строка с #cached-token рядом с этим временем")
