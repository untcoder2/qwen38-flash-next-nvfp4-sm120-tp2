#!/usr/bin/env bash
# Наблюдатель за заходом друга: его TCP внутри туннеля (wg1, порт 1234) +
# POST'ы, дошедшие до sglang. Имя файла N секунд. Запуск от root.
set -u
N="${1:-300}"
OUT=/home/molochko/agent-tools/friend-watch.log
: > "$OUT"
echo "watch start $(date -u +%FT%TZ), окно ${N}s" >> "$OUT"
timeout "$N" tcpdump -ni wg1 -l "host 10.9.0.2" 2>/dev/null >> "$OUT" &
P1=$!
( timeout "$((N+5))" docker logs -f penny --since 1s 2>&1 | grep --line-buffered -E "POST /v1/chat|GET /v1/models|400 Bad|499|abort" >> "$OUT" ) &
P2=$!
sleep "$N"
kill "$P1" "$P2" 2>/dev/null
{
  echo "== итог =="
  echo "tcp-строк от друга: $(grep -c "10.9.0.2" "$OUT" || true)"
  echo "POST до sglang:     $(grep -c "POST /v1/chat" "$OUT" || true)"
} >> "$OUT"
chmod 644 "$OUT" 2>/dev/null
echo WATCH-DONE
