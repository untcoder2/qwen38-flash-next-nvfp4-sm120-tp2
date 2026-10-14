#!/usr/bin/env python3
"""cache_trace_read.py — разбор cache_trace.jsonl: где именно рвётся префикс между ходами.

Сравнивает ТОЛЬКО запросы одного типа (боевой /v1/messages с боевым, count_tokens с count_tokens) —
первая версия сравнивала подряд идущие запросы вообще и на служебных count_tokens выдавала
ложное «клиент переписывает историю».

Для каждого боевого хода: индекс первого отличившегося сообщения, сколько байтов истории после
него (это и есть минимальный ре-префилл), и чем отличается голова (system/tools/заголовки).

Локально:  python -I ops/cache_trace_read.py tmp/cache_trace.jsonl
На хосте:  python3 -I ~/agent-tools/cache_trace_read.py
"""
import json
import sys

PATH = sys.argv[1] if len(sys.argv) > 1 else "/home/molochko/agent-tools/cache_trace.jsonl"
MAIN = "/v1/messages"

rows = []
with open(PATH, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass

groups = {}
for r in rows:
    groups.setdefault((r.get("path") or "-").split("?")[0], []).append(r)

other = {k: len(v) for k, v in groups.items() if k != MAIN}
if other:
    print("служебные (не сравниваются с боевыми):", ", ".join(f"{k} ×{v}" for k, v in other.items()))

main = groups.get(MAIN) or [r for r in rows if r.get("path", "").startswith(MAIN)]
if not main:
    print(f"боевых {MAIN} в логе нет — гоняй сессию")
    sys.exit(0)

print(f"\n=== {MAIN}: {len(main)} боевых запросов ===")
print(f"{'ход':>3} {'сообщ.':>7} {'байт':>9}  {'перв.расхожд.':<13} {'после него':>10}  голова")
prev = None
tails = []
for i, r in enumerate(main):
    head = []
    if prev:
        if r.get("sys_h") != prev.get("sys_h"):
            head.append("system≠")
        if r.get("tools_h") != prev.get("tools_h"):
            head.append("tools≠")
        if r.get("hdr_h") and prev.get("hdr_h") and r["hdr_h"] != prev["hdr_h"]:
            head.append("headers≠")
        a, b = prev.get("msgs_h") or [], r.get("msgs_h") or []
        n = min(len(a), len(b))
        d = next((k for k in range(n) if a[k] != b[k]), None)
        if d is None:
            diff = "нет" if len(a) == len(b) else f"append +{len(b) - len(a)}"
            tail = 0
        else:
            diff = str(d)
            tail = sum((r.get("msg_chars") or [])[d:])
            tails.append(tail)
        print(
            f"{i:>3} {r.get('n_msgs', 0):>7} {r.get('bytes', 0):>9,}  {diff:<13} {tail:>10,}  "
            + (", ".join(head) if head else "head стабильна")
        )
        if r.get("div_prev"):
            print(f"      БЫЛО : {r['div_prev']}")
            print(f"      СТАЛО: {r['div_cur']}")
    else:
        print(f"{i:>3} {r.get('n_msgs', 0):>7} {r.get('bytes', 0):>9,}  {'-':<13} {'-':>10}  базовый")
    prev = r

if tails:
    tails.sort()
    print(
        f"\nхвост после точки расхождения: медиана {tails[len(tails) // 2]:,} байт, "
        f"макс {tails[-1]:,} байт ({len(tails)} ход(ов) с расхождением из {len(main) - 1})"
    )
    print("0 байт = чистый append; в байтах, дели на ~3 для грубой оценки токенов")
