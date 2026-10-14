#!/usr/bin/env bash
# Финализатор TLS-переключения: ждёт https-здоровье инфера, снимает .maintenance.
set -u
LOG=/home/molochko/vllm/tls-switch.log
CA=/home/molochko/penny/tls/ca.pem
for i in $(seq 1 60); do
  sleep 10
  if curl -sf -m 8 --cacert "$CA" https://127.0.0.1:1234/health >/dev/null 2>&1; then
    echo "$(date) TLS_HEALTH_OK after $((i*10))s" >> "$LOG"
    rm -f /home/molochko/vllm/.maintenance
    echo "$(date) .maintenance cleared" >> "$LOG"
    exit 0
  fi
done
echo "$(date) TLS_HEALTH_TIMEOUT (maintenance остаётся висеть!)" >> "$LOG"
exit 1
