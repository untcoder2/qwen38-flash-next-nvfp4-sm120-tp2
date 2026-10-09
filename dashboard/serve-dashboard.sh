#!/bin/bash
# Watchdog статического сервера дашборда. Ставится в cron: @reboot + */5.
# Отличается от sglang-листов: не трогает инфер, юниты, active.cfg — просто http.server.
DIR=/home/molochko/dashboard
PORT=8080
IP=192.168.2.127
if pgrep -f "[s]erve_dashboard\.py" >/dev/null 2>&1; then
  exit 0
fi
cd "$DIR" || exit 1
setsid /usr/bin/python3 "$DIR/serve_dashboard.py" >>"$DIR/http.log" 2>&1 < /dev/null &
echo "$(date '+%F %T') serve_dashboard.py:$PORT поднят" >> "$DIR/http.log"
