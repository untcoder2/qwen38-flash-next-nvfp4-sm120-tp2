#!/bin/bash
# vLLM watchdog v4: venv health + ports 1234/1235 + maintenance mode.
# Runs under systemd (vllm-watchdog.service).
LOG=/home/molochko/vllm/watch.log
SERVE_DIR=/home/molochko/vllm
ACTIVE=$SERVE_DIR/active.cfg
MAINT=$SERVE_DIR/.maintenance
VENV_PY=/home/molochko/vllm/env/bin/python
h() { curl -sf -m 5 --cacert /home/molochko/penny/tls/ca.pem "https://127.0.0.1:$1/health" >/dev/null 2>&1 || curl -sf -m 5 --cacert /home/molochko/penny/tls/ca.pem "https://127.0.0.1:$1/v1/models" >/dev/null 2>&1; }
stale_pid() {
  for p in $(pgrep -f "$1"); do
    et=$(ps -o etimes= -p "$p" 2>/dev/null | tr -d ' ')
    if [ -n "$et" ] && [ "$et" -gt 700 ]; then echo "$p"; return; fi
  done
  return 1
}
venv_ok() { "$VENV_PY" -c "import vllm" >/dev/null 2>&1; }
reinstall_if_broken() {
  if venv_ok; then return 0; fi
  MARK=$SERVE_DIR/.last-reinstall
  NOW=$(date +%s); LAST=$(cat "$MARK" 2>/dev/null || echo 0)
  if [ $((NOW - LAST)) -gt 1800 ]; then
    echo "$(date) VENV BROKEN -> install.sh (cooldown 30m)" >> "$LOG"
    echo "$NOW" > "$MARK"
    bash $SERVE_DIR/install.sh >> $SERVE_DIR/install.log 2>&1
    venv_ok && echo "$(date) VENV RESTORED" >> "$LOG" || echo "$(date) VENV STILL BROKEN" >> "$LOG"
  fi
  venv_ok
}
# Cap the sglang scheduler busy-poll child to 50% of one core (cgroup v2).
# Self-heals after restart; no-op if already capped. Root via NOPASSWD helper.
CPU_CAP_CG=/sys/fs/cgroup/sglang-cpu
apply_cpu_cap() { sudo -n /home/molochko/vllm/cpu-cap.sh >/dev/null 2>&1 || true; }
echo $$ > /home/molochko/vllm/watch.pid
echo "watchdog v4 started $(date) pid=$$" >> "$LOG"
while true; do
  if [ -f "$MAINT" ]; then sleep 15; continue; fi
  apply_cpu_cap
  if ! h 1234; then
    SP=$(stale_pid '((vllm serve .*--port 1234|sglang serve .*--port 1234))')
    if [ -n "${SP:-}" ]; then
      echo "$(date) 1234 HUNG (pid $SP) -> kill + restart" >> "$LOG"; kill -9 "$SP" 2>/dev/null; sleep 3
    elif ! pgrep -f 'vllm serve .*--port 1234' >/dev/null && ! pgrep -f 'sglang serve' >/dev/null; then
      if reinstall_if_broken; then
        echo "$(date) 1234 DOWN -> restart" >> "$LOG"
        SCRIPT=$(cat "$ACTIVE" 2>/dev/null || echo serve.sh)
        setsid nohup bash "$SERVE_DIR/$SCRIPT" >> "$SERVE_DIR/serve-watchdog.out" 2>&1 &
        echo "$(date) started $SCRIPT pid=$!" >> "$LOG"
      fi
    fi
  fi
  sleep 20
done
