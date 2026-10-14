#!/bin/bash
# Страховка 20:00: если оркестратор умер молча и прод в простое — поднять что было.
crontab -l 2>/dev/null | grep -v 'insure_prod.sh' | crontab -
S=/home/molochko/penny/tp2-serve-flash-next.sh
if ! grep -q "ORCHESTRATOR-\(DONE\|FAILED\)" /home/molochko/mtp-work/STATUS 2>/dev/null; then
  pgrep -f orchestrator.sh >/dev/null || {
    echo "[$(date -Is)] INSURANCE: оркестратор мёртв, прод не поднят — принудительный рестарт" >> /home/molochko/mtp-work/STATUS
    cp /home/molochko/mtp-work/sheet-before.sh "$S" 2>/dev/null
    rm -f /home/molochko/vllm/.maintenance
    sudo -n systemctl restart sglang
  }
fi
