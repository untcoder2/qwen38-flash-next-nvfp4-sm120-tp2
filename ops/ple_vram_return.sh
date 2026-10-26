#!/usr/bin/env bash
# ple_vram_return.sh — деплой профиля PLE-VRAM на живом sglang (волна hc30/mf0.93,
# owner decision 2026-10-26 ~12:20): --no-ple-offload-embedding (без изменений),
# --mem-fraction-static 0.93, --hicache-size 30, chunked-prefill 8192 (без изменений).
# Пред. волна: hc24/mf0.92 (реверс на неё — env'ом: PLE_RETURN_PRE=...bak-plevram-20261026-040737
#   PLE_RETURN_MF=0.92 PLE_RETURN_HC=24 PLE_RETURN_OLD_MF=0.91 PLE_RETURN_OLD_HC=4).
# Обратный к ple_ram_switch.sh (там было VRAM->RAM; здесь RAM->VRAM + рост профиля).
# Правки в tp2-serve-flash-next.sh УЖЕ внесены до ревью-гейта; лист их НЕ применяет:
# PREFLIGHT проверяет маркеры и ABORT-ит, если правки не на месте.
# Откат — СТРОГО на known-good PRE (rev.2 #1; в rev.3 — путь волны hc30, env PLE_RETURN_PRE).
# Своего BK из уже-правленого файла не создаём — источник отката один; отвергнутый профиль
# сохраняется в $SERVE.failed-<TS> перед затиранием (rev.4 R1).
# rev.2 фиксы: journalctl без sudo (#2, molochko в adm); rollback stop/cp/start с проверкой кода
# и reset-failed перед start, rb_ok-цикл (#3); signal-trap до restart, снимается в rollback и перед
# DONE (#4); RESTART_TS после restart, NRestarts canary + is-failed (#5); ровно 1 PID по полному
# пути (#6); drop_caches '%s\n' (#7, sec#6); preflight: ca.pem, reset-failed перед restart,
# двойные маркеры new+!old (#8, sec#7); ассерты поэлементно tr '\0' '\n' | grep -qxF (#9).
# rev.3: значения волны — константами сверху (env-override), PRE-верификация — парой
# $OLD_MF/$OLD_HC (RAM-era маркеры убраны, принадлежали волне plevram).
# rev.4 (review round 2): SOAK 30x10s после assert'ов до DONE — обязателен, выключателя нет
# (F1/F6: анкоренный OOM-греп, is-failed, NRestarts>N0, MemAvailable<6G, лог каждой 5-й);
# PRE-верификация: -qE с \b (F2) + VRAM гейт через PLE_RETURN_PLE (N1) + лог цели PRE;
# гейт числовых форматов констант волны (F3); pgrep [s]glang (F4/R2); stash .failed-<TS>
# первой строкой rollback (R1). Signal-ловушка активна на всём soak: сигнал в soak -> exit 129,
# это осознанно (юнит тогда мог ещё не пережить soak — ручная проверка status обязательна).
# Идемпотентность: повторный запуск безопасен. Лог: ~/penny/ops/ple-vram.log
set -u
P=/home/molochko/penny; OP=$P/ops
SERVE=$P/tp2-serve-flash-next.sh
LOG=$OP/ple-vram.log
PWFILE=$HOME/agent-tools/.sudo_pw
# --- константы волны (переопределяются env'ом; дефолты = волна hc30/mf0.93) ---
PRE="${PLE_RETURN_PRE:-$P/tp2-serve-flash-next.sh.bak-hc30-20261026-122400}"   # known-good = 0.92/24
MF="${PLE_RETURN_MF:-0.93}"          # новый mem-fraction-static
HC="${PLE_RETURN_HC:-30}"            # новый hicache-size
CHUNK="${PLE_RETURN_CHUNK:-8192}"    # chunked-prefill (в этой волне не менялся)
# старые маркеры PRE (то, чего в новом профиле быть не должно):
OLD_MF="${PLE_RETURN_OLD_MF:-0.92}"
OLD_HC="${PLE_RETURN_OLD_HC:-24}"
log(){ echo "$(date '+%m-%d %H:%M:%S') $*" | tee -a "$LOG"; }
h(){ curl -sf -m 8 --cacert "$P/tls/ca.pem" https://127.0.0.1:1234/health_generate >/dev/null; }
# F3: гейт форматов констант волны (сразу после log(), чтобы ABORT попал в журнал)
[[ "$MF" =~ ^0\.[0-9]{2}$ ]] && [[ "$HC" =~ ^[1-9][0-9]{0,2}$ ]] && [[ "$CHUNK" =~ ^[0-9]{4,6}$ ]] || { log "ABORT: нечисловые константы волны"; exit 1; }

rollback(){
  cp -a "$SERVE" "$SERVE.failed-$(date +%Y%m%d-%H%M%S)" || true
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
# PRE = профиль пред. волны: старый MF и старый hicache присутствуют, новый hicache — нет (F2: -qE+\b)
grep -qE -- "--mem-fraction-static ${OLD_MF}\b" "$PRE" || { log "ABORT: PRE не known-good (нет --mem-fraction-static $OLD_MF)"; exit 1; }
grep -qE -- "--hicache-size ${OLD_HC}\b" "$PRE" || { log "ABORT: PRE не known-good (нет --hicache-size $OLD_HC)"; exit 1; }
grep -qE -- "--hicache-size ${HC}\b" "$PRE" && { log "ABORT: PRE не known-good (в PRE уже --hicache-size $HC)"; exit 1; }
# N1: PRE обязан быть VRAM-профилем (гейт PLE_RETURN_PLE=vram по умолчанию; иное значение = misuse -> ABORT)
[ "${PLE_RETURN_PLE:-vram}" = vram ] && grep -qF -- '--no-ple-offload-embedding' "$PRE" || { log "ABORT: PRE не VRAM-профиль"; exit 1; }
log "PRE=$PRE: $(grep -oE -- '--mem-fraction-static [0-9.]+|--hicache-size [0-9]+|--(no-)?ple-offload-embedding' "$PRE" | tr '\n' ' ')"
# новые маркеры присутствуют И старые отсутствуют (sec#7; \b — чтобы 24 не матчил 240)
grep -qE -- "--mem-fraction-static ${MF}\b" "$SERVE" || { log "ABORT: маркер $MF не найден"; exit 1; }
grep -qE -- "--hicache-size ${HC}\b" "$SERVE" || { log "ABORT: маркер hicache-size $HC не найден"; exit 1; }
grep -qE "^PREFILL_CHUNK_SIZE=${CHUNK}\$" "$SERVE" || { log "ABORT: маркер PREFILL_CHUNK_SIZE=$CHUNK не найден"; exit 1; }
grep -qF 'PLE_ARGS=(--no-ple-offload-embedding)' "$SERVE" || { log "ABORT: маркер PLE_ARGS=(--no-ple-offload-embedding) не найден"; exit 1; }
grep -qE -- "--mem-fraction-static ${OLD_MF}\b" "$SERVE" && { log "ABORT: старый $OLD_MF остался в $SERVE"; exit 1; }
grep -qE -- "--hicache-size ${OLD_HC}\b" "$SERVE" && { log "ABORT: старый hicache-size $OLD_HC остался в $SERVE"; exit 1; }
log "PREFLIGHT OK: PRE known-good, волна MF=$MF HC=$HC CHUNK=$CHUNK+PLE-VRAM, старых ($OLD_MF/$OLD_HC) нет, bash -n чистый"

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

# --- 5) ASSERTS: ровно один процесс, cmdline поэлементно (#6/#9; F4: [s]glang против самохвата) ---
mapfile -t PIDS < <(pgrep -f "/opt/pennyroyal/.venv/bin/[s]glang serve" || true)
if [ "${#PIDS[@]}" -ne 1 ]; then
  log "ASSERT FAIL: совпадений 'sglang serve': ${#PIDS[@]} (${PIDS[*]:-none})"
  rollback "не один процесс sglang serve — ассерты по чужому cmdline недопустимы"
fi
PID=${PIDS[0]}
CMEL=$(tr '\0' '\n' < "/proc/$PID/cmdline")
el(){ printf '%s\n' "$CMEL" | grep -qxF -- "$1"; }
fail=0
if el '--no-ple-offload-embedding'; then log "ASSERT OK: PLE in VRAM"; else log "ASSERT FAIL: --no-ple-offload-embedding"; fail=1; fi
if el '--mem-fraction-static' && el "$MF"; then log "ASSERT OK: mem-fraction $MF"; else log "ASSERT FAIL: пара --mem-fraction-static/$MF"; fail=1; fi
if el '--hicache-size' && el "$HC"; then log "ASSERT OK: hicache-size $HC"; else log "ASSERT FAIL: пара --hicache-size/$HC"; fail=1; fi
if el '--chunked-prefill-size' && el "$CHUNK"; then log "ASSERT OK: chunked-prefill $CHUNK"; else log "ASSERT FAIL: пара --chunked-prefill-size/$CHUNK"; fail=1; fi
log "asserts: PID=$PID fail=$fail"
[ "$fail" = 0 ] || rollback "cmdline PID=$PID не соответствует новому профилю"

# --- 6) POST-ЛОГ (journal без sudo, #2) ---
log "пост-состояние:"
journalctl -u sglang --since "$RESTART_TS" --no-pager 2>/dev/null \
  | grep -iE "max_total_num_tokens|available_gpu_mem" | tail -8 >>"$LOG"
{ nvidia-smi --query-gpu=memory.used,memory.total --format=csv; free -h; } >>"$LOG" 2>&1

# --- 7) SOAK 30x10s (F1; обязателен, выключателя нет). Ловушка активна: сигнал в soak -> exit 129
# (осознанно: профиль ещё не пережил soak). OOM-греп анкоренный (F6). ---
for i in $(seq 1 30); do
  sleep 10
  if journalctl -u sglang --since "$RESTART_TS" --no-pager 2>/dev/null \
     | grep -qE "ValueError: Not enough host memory|torch\.cuda\.OutOfMemoryError|CUDA out of memory"; then
    rollback "post-DONE soak: OOM"
  fi
  if [ "$(systemctl is-failed sglang)" = "failed" ]; then rollback "post-DONE soak: unit is-failed"; fi
  N=$(systemctl show sglang -p NRestarts --value)
  if [ "${N:-0}" -gt "$N0" ]; then rollback "post-DONE soak: NRestarts +$N"; fi
  MA=$(awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo)
  if [ "${MA:-999}" -lt 6 ]; then rollback "soak: MemAvailable ${MA}G"; fi
  if [ $((i % 5)) -eq 0 ]; then
    log "soak $i: MA=${MA}G nvidia-used=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader | head -1)"
  fi
done
log "PLE_VRAM_RETURN=SOAK_OK"
trap - HUP INT TERM PIPE
log "PLE_VRAM_RETURN=DONE"
exit 0
