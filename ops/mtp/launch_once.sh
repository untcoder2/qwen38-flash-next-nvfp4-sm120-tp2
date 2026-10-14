#!/bin/bash
# Разовый стартер: убирает свою cron-строку, глушит GUI-риск (прод стопится,
# когда сессия Kimi гарантированно простаивает), запускает оркестратор.
crontab -l 2>/dev/null | grep -v 'launch_once.sh' | crontab -
cd /home/molochko/mtp-work
echo "[$(date -Is)] launcher: cron-fired, starting orchestrator" >> STATUS
nohup bash orchestrator.sh > orch-boot.log 2>&1 < /dev/null &
