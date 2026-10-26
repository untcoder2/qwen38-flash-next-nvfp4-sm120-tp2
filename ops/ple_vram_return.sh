#!/usr/bin/env bash
# ple_vram_return.sh — возврат конфига PLE в VRAM (owner decision 2026-10-26 ~04:10):
#   --no-ple-offload-embedding, --mem-fraction-static 0.92, --hicache-size 24, chunked-prefill 8192.
# Обратный к ple_ram_switch.sh (там было VRAM->RAM; здесь RAM->VRAM + рост профиля).
# Правки в tp2-serve-flash-next.sh УЖЕ внесены до ревью-гейта; лист их НЕ применяет:
# PREFLIGHT проверяет маркеры и ABORT-ит, если правки не на месте.
# Откат — СТРОГО на known-good PRE (rev.2 #1): tp2-serve-flash-next.sh.bak-plevram-20261026-040737.
# Своего BK из уже-правленого файла не создаём — источник отката один.
# rev.2 фиксы: journalctl без sudo (#2, molochko в adm); rollback stop/cp/start с проверкой кода
# и reset-failed перед start, rb_ok-цикл (#3); signal-trap до restart, снимается в rollback и перед
# DONE (#4); RESTART_TS после restart, NRestarts canary + is-failed (#5); ровно 1 PID по полному
# пути (#6); drop_caches '%s\n' (#7, sec#6); preflight: ca.pem, reset-failed перед restart,
# двойные маркеры new+!old (#8, sec#7); ассерты поэлементно tr '\0' '\n' | grep -qxF (#9).
# ОТКЛОНИЕНИЕ от списка A#1 (для раунда 2): literal '^PLE_ARGS=(--ple-offload-embedding)$' в PRE
# нет — serve-файл никогда его не определял (RAM-дефолт шёл из image ple-backend.sh, строка-коммент
# "ple-оверрай больше не монтируется"). Заменено на равносильную пару: коммент RAM-эпохи в PRE есть
# И --no-ple-offload-embedding в PRE нет.
# Идемпотентность: повторный запуск безопасен. Лог: ~/penny/ops/ple-vram.log
set -u
P=/home/molochko/penny; OP=$P/ops
SERVE=$P/tp2-serve-flash-next.sh
PRE=$P/tp2-serve-flash-next.sh.bak-plevram-20261026-040737
LOG=$OP/ple-vram.log
PWFILE=$HOME/agent-tools/.sudo_pw
log(){ echo "$(date '+%m-%d %H:%M:%S') $*" | tee -a "$LOG"; }
h(){ curl -sf -m 8 --cacert "$P/tls/ca.pem" https://127.0.0.1:1234/health_generate >/dev/null; }

rollback(){
  trap - HUP INT TERM PIPE
  log "ROLLBACK: $1"
  sudo -n systemctl stop sglang || { log "ROLLBACK FAILED: stop"; exit 3; }
  cp -a "$PRE" "$SERVE" || { log "ROLLBACK FAILED: cp PRE->SERVE"; exit 3; }
  sudo -n systemctl reset-failed sglang || log "WARN: reset-failed перед start не прошёл"
  sudo -n systemctl start sglang || { log "ROLLBACK FAILED: start"; exit 3; }
  rb_ok=0
  for i in $(seq 1 120); do sleep 10; if h; then rb_ok=1; log "ROLLBACK HEALTH_OK ($((i*10))s)"; break; fi; done
  if [ "$rb_ok" = 0 ]; then log "ROLLBACK_FAILED (health не вернулся)"; exit 3; fi
  log "PLE_VRAM_RETURN=ROLLED_BACK"
  exit 2
}

# --- 1) PREFLIGHT: PRE known-good + правки на месте (иначе ABORT, ничего не трогаем) ---
[ -f "$SERVE" ] || { log "ABORT: нет $SERVE"; exit 1; }
bash -n "$SERVE" || { log "ABORT: bash -n на $SERVE"; exit 1; }
[ -r "$P/tls/ca.pem" ] || { log "ABORT: нет/не читается $P/tls/ca.pem"; exit 1; }
[ -f "$PRE" ] || { log "ABORT: нет known-good $PRE"; exit 1; }
grep -qF -- '--mem-fraction-static 0.91' "$PRE" || { log "ABORT: PRE не known-good (нет 0.91)"; exit 1; }
grep -qE '^PREFILL_CHUNK_SIZE=16384$' "$PRE" || { log "ABORT: PRE не known-good (нет 16384)"; exit 1; }
grep -q 'ple-оверрай больше не монтируется' "$PRE" || { log "ABORT: PRE не known-good (нет RAM-коммента)"; exit 1; }
grep -qF -- '--no-ple-offload-embedding' "$PRE" && { log "ABORT: PRE не known-good (в PRE VRAM-флаг)"; exit 1; }
# новые маркеры присутствуют И старые отсутствуют (sec#7)
grep -qF -- '--mem-fraction-static 0.92' "$SERVE" || { log "ABORT: маркер 0.92 не найден"; exit 1; }
grep -qF -- '--hicache-size 24' "$SERVE" || { log "ABORT: маркер hicache-size 24 не найден"; exit 1; }
grep -qE '^PREFILL_CHUNK_SIZE=8192$' "$SERVE" || { log "ABORT: маркер PREFILL_CHUNK_SIZE=8192 не найден"; exit 1; }
grep -qF 'PLE_ARGS=(--no-ple-offload-embedding)' "$SERVE" || { log "ABORT: маркер PLE_ARGS=(--no-ple-offload-embedding) не найден"; exit 1; }
grep -qF -- '--mem-fraction-static 0.91' "$SERVE" && { log "ABORT: старый 0.91 остался в $SERVE"; exit 1; }
grep -qE '^PREFILL_CHUNK_SIZE=16384$' "$SERVE" && { log "ABORT: старый 16384 остался в $SERVE"; exit 1; }
log "PREFLIGHT OK: PRE known-good, 4/4 маркеров, старых нет, bash -n чистый"

# --- 2) drop_caches (warn-only), пароль не светить (sec#6: '%s\n') ---
[ -r "$PWFILE" ] || { log "ABORT: нет $PWFILE"; exit 1; }
S=$(cat "$PWFILE")
printf '%s\n' "$S" | sudo -S -p "" bash -c 'sync; echo 3 > /proc/sys/vm/drop_caches' >>"$LOG" 2>&1 || log "WARN: drop_caches не прошёл"
unset S; sleep 2

# --- 3) restart (trap до точки невозврата, #4/#5) ---
trap 'log "ABORTED by signal — проверь systemctl status sglang; при down: reset-failed + start"; exit 129' HUP INT TERM PIPE
sudo -n systemctl reset-failed sglang || log "WARN: reset-failed перед restart не прошёл"
N0=$(systemctl show sglang -p NRestarts --value); N0=${N0:-0}
log "restart sglang (NRestarts before=$N0)"
sudo -n systemctl restart sglang || rollback "systemctl restart sglang вернул код != 0"
RESTART_TS=$(date '+%Y-%m-%d %H:%M:%S')
log "restart вернул, журнал с $RESTART_TS"

# --- 4) WATCH: 150x10s; ранний OOM (journal без sudo, #2), crash-loop/failed canary (#5) ---
ok=0
for i in $(seq 1 150); do
  sleep 10
  if journalctl -u sglang --since "$RESTART_TS" --no-pager 2>/dev/null \
     | grep -qiE "Not enough host memory|out of (CUDA )?memory"; then
    log "EARLY-ABORT: OOM-маркер в журнале на итерации $i ($((i*10))s)"
    rollback "ранний OOM-маркер в журнале"
  fi
  N=$(systemctl show sglang -p NRestarts --value)
  if [ "${N:-0}" -gt "$((N0+2))" ]; then log "CRASH-LOOP: NRestarts $N0 -> $N"; rollback "crash-loop unit"; fi
  if [ "$(systemctl is-failed sglang)" = "failed" ]; then rollback "unit is-failed"; fi
  if h; then ok=1; log "HEALTH_OK ($((i*10))s)"; break; fi
done
[ "$ok" = 1 ] || rollback "UNHEALTHY 1500s"

# --- 5) ASSERTS: ровно один процесс, cmdline поэлементно (#6/#9) ---
mapfile -t PIDS < <(pgrep -f "/opt/pennyroyal/.venv/bin/sglang serve" || true)
if [ "${#PIDS[@]}" -ne 1 ]; then
  log "ASSERT FAIL: совпадений 'sglang serve': ${#PIDS[@]} (${PIDS[*]:-none})"
  rollback "не один процесс sglang serve — ассерты по чужому cmdline недопустимы"
fi
PID=${PIDS[0]}
CMEL=$(tr '\0' '\n' < "/proc/$PID/cmdline")
el(){ printf '%s\n' "$CMEL" | grep -qxF -- "$1"; }
fail=0
if el '--no-ple-offload-embedding'; then log "ASSERT OK: PLE in VRAM"; else log "ASSERT FAIL: --no-ple-offload-embedding"; fail=1; fi
if el '--mem-fraction-static' && el '0.92'; then log "ASSERT OK: mem-fraction 0.92"; else log "ASSERT FAIL: пара --mem-fraction-static/0.92"; fail=1; fi
if el '--hicache-size' && el '24'; then log "ASSERT OK: hicache-size 24"; else log "ASSERT FAIL: пара --hicache-size/24"; fail=1; fi
if el '--chunked-prefill-size' && el '8192'; then log "ASSERT OK: chunked-prefill 8192"; else log "ASSERT FAIL: пара --chunked-prefill-size/8192"; fail=1; fi
log "asserts: PID=$PID fail=$fail"
[ "$fail" = 0 ] || rollback "cmdline PID=$PID не соответствует новому профилю"

# --- 6) POST-ЛОГ (journal без sudo, #2) ---
log "пост-состояние:"
journalctl -u sglang --since "$RESTART_TS" --no-pager 2>/dev/null \
  | grep -iE "max_total_num_tokens|available_gpu_mem" | tail -8 >>"$LOG"
{ nvidia-smi --query-gpu=memory.used,memory.total --format=csv; free -h; } >>"$LOG" 2>&1
trap - HUP INT TERM PIPE
log "PLE_VRAM_RETURN=DONE"
exit 0
