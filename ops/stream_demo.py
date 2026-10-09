#!/usr/bin/env python3
"""Живой стрим токенов с инфер-сервера в консоль — для замера/съёмки скорости.
Открыть в отдельном окне терминала, запустить, снять экран вместе с виджетом."""
import json, sys, time, urllib.request

BASE = "http://192.168.2.127:1234"
PROMPT = ("Напиши большой подробный рассказ про рыцаря, который идёт через "
          "неоновое подземелье: описания, диалоги, магия, битвы. Не менее 2000 слов.")

def stream(n_tokens=3000, effort=None):
    ctk = {"enable_thinking": True, "preserve_thinking": True}
    if effort:
        ctk["reasoning_effort"] = effort
    body = {"text": PROMPT,
            "sampling_params": {"temperature": 0.8, "max_new_tokens": n_tokens},
            "stream": True,
            "chat_template_kwargs": ctk}
    req = urllib.request.Request(BASE + "/generate",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = got = 0
    with urllib.request.urlopen(req, timeout=300) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                chunk = json.loads(line[5:])
            except Exception:
                continue
            piece = chunk.get("text", "")
            if piece:
                if not t0:
                    t0 = time.time()
                got += len(piece)
                sys.stdout.write(piece)
                sys.stdout.flush()
            if chunk.get("meta_info", {}).get("finish_reason"):
                break
    dt = time.time() - t0
    # грубая прикидка: ~3.8 символа на токен для смесь RU/EN
    print(f"\n\n--- ok: {got} chars in {dt:.1f}s ~ {got/3.8/max(dt,0.001):.0f} tok/s ---")

if __name__ == "__main__":
    effort = sys.argv[1] if len(sys.argv) > 1 else None
    stream(effort=effort)
