#!/bin/bash
# Установка cron-обвязки дашборда (без root): авторефреш + watchdog http-сервера.
set -e
DIR=/home/molochko/dashboard
( crontab -l 2>/dev/null | grep -vF "$DIR"
  echo "0 10,20 * * * cd $DIR && /usr/bin/python3 refresh.py >> $DIR/refresh.log 2>&1"
  echo "*/30 * * * * /usr/bin/python3 $DIR/tick_counter.py >> $DIR/tick.log 2>&1"
  echo "*/5 * * * * $DIR/serve-dashboard.sh"
  echo "@reboot $DIR/serve-dashboard.sh"
) | crontab -
echo "cron-записей dashboard: $(crontab -l | grep -c "$DIR")"
