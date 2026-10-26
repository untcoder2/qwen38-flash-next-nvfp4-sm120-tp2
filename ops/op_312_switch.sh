#!/usr/bin/env bash
# op_312_switch.sh — свитч образа penny v3.1.1@f2a6a8ca… -> v3.1.2@013a685d… через active.cfg
# + restart sglang. Профиль tp2-serve-flash-next.sh НЕ меняется (доказано A/B: sourced-хелперы
# SAME, кроме аддитивного startup-summary; поведение 1:1). Откат = вернуть active.cfg из
# .bak-312-<TS> + restart; свитч парной отмены профиля не требует.
# ОТКЛОНЕНИЕ от спека assert (a): формат баннера "Penny Royal 3.1.2" в журнале НЕ существует —
# проверено на текущем boot юнита (стартовый баннер: "Pennyroyal profile: Flash-Next ...",
# версия в stdout контейнера не печатается, в journal за 7 дней совпадений "3.1.[0-9]" нет).
# Заменил на равносильный якорь версии: строка баннера "Pennyroyal profile: Flash-Next" с
# RESTART_TS (подтверждение свежего старта) + полный pin в Config.Image контейнера (c).
# Совпадение "Penny Royal 3.1.2", если когда-нибудь появится, пишется в лог best-effort, не краснь.
# rev.2 (раунд 1 ревью): +SOAK 30x10s перед DONE (F1/N3 блокер; база NRestarts на входе в soak);
# +пин image-строки и md5 листа 312 в preflight (N1); +md5-пин профиля (N4); +негатив-греп
# MTP-union mismatch в assert (a2) (F2-ii); rollback read-back cfg + CRITICAL-форма restores
# (N2/nit-5); расширенный текст ABORT «уже 312» (nit-2).
# Автократ: ABORT до первого изменения при любом red preflight/snapshot; после switch — rollback
# на UNHEALTHY 1500s / анкоренный OOM / is-failed / crash-loop / провал assert'ов / провал soak.
# Лог: ~/penny/ops/upgrade312.log. Дисциплина trap/rollback — как ple_vram_return.sh rev.4.
set -u
P=/home/molochko/penny; OP=$P/ops; CD=/home/molochko/vllm
CFG=$CD/active.cfg
OLD=serve-penny-311w4.sh
NEW=serve-penny-312.sh
SHEET=$CD/$NEW
PIN=sha256:013a685d042b178842cbf016e7deb8d23113b87044355ef785dbee6d56e57db6
IMG="ghcr.io/jpezzulli/sglang-rtxpro6000:v3.1.2@$PIN"
# md5-пины (раунд 1, N1/N4): лист 312 после коммент-правки B8; профиль после коммент-правки B9
SHEET_MD5_WANTED=1a535e91efe1b23e17e1b01d0042342a
PROF_MD5_WANTED=3347ef31f882a7a51f9527fa738462f3
LOG=$OP/upgrade312.log
PWFILE=$HOME/agent-tools/.sudo_pw
TS=$(date +%Y%m%d-%H%M%S)
BAK=$CFG.bak-312-$TS
log(){ echo "$(date '+%m-%d %H:%M:%S') $*" | tee -a "$LOG"; }
h(){ curl -sf -m 8 --cacert "$P/tls/ca.pem" https://127.0.0.1:1234/health_generate >/dev/null; }

rollback(){
  trap - HUP INT TERM PIPE
  log "ROLLBACK: $1"
  sudo -n systemctl stop sglang || { log "ROLLBACK FAILED: stop"; exit 3; }
  cp -a "$BAK" "$CFG" || { log "ROLLBACK FAILED: cp cfg->active"; exit 3; }
  [ "$(cat "$CFG")" = "$OLD" ] || { log "ROLLBACK FAILED: cfg не восстановлен"; exit 3; }
  sudo -n systemctl reset-failed sglang || log "WARN: reset-failed перед start не прошёл"
  sudo -n systemctl start sglang || { log "ROLLBACK FAILED: start"; exit 3; }
  rb_ok=0
  for i in $(seq 1 120); do sleep 10; if h; then rb_ok=1; log "ROLLBACK HEALTH_OK ($((i*10))s)"; break; fi; done
  if [ "$rb_ok" = 0 ]; then log "ROLLBACK_FAILED (health не вернулся)"; exit 3; fi
  log "UPGRADE312=ROLLED_BACK"
  exit 2
}

# --- 1) PREFLIGHT (всё read-only; любой краснь = ABORT до первого изменения) ---
[ -r "$P/tls/ca.pem" ] || { log "ABORT: нет/не читается $P/tls/ca.pem"; exit 1; }
[ -r "$PWFILE" ] || { log "ABORT: нет/не читается $PWFILE"; exit 1; }
h || { log "ABORT: health_generate НЕ зелёный до начала — не чиним вслепую"; exit 1; }
[ -f "$SHEET" ] || { log "ABORT: нет нового листа $SHEET"; exit 1; }
bash -n "$SHEET" || { log "ABORT: bash -n на $SHEET"; exit 1; }
# N1: лист обязан нести точный image-pin и быть тем самым файлом, что прошёл ревью
grep -qF -- "v3.1.2@sha256:013a685d042b178842cbf016e7deb8d23113b87044355ef785dbee6d56e57db6" "$SHEET" \
  || { log "ABORT: в $SHEET нет строки v3.1.2@013a685d…"; exit 1; }
SHEET_MD5=$(md5sum "$SHEET" | cut -d' ' -f1)
[ "$SHEET_MD5" = "$SHEET_MD5_WANTED" ] || { log "ABORT: md5 листа 312 = $SHEET_MD5 != пин $SHEET_MD5_WANTED"; exit 1; }
# N4: профиль — pinned коммент-ревизия (флаг-строки инвариантны; правка доехала без рестарта)
PROF_MD5=$(md5sum "$P/tp2-serve-flash-next.sh" | cut -d' ' -f1)
[ "$PROF_MD5" = "$PROF_MD5_WANTED" ] || { log "ABORT: md5 профиля = $PROF_MD5 != пин $PROF_MD5_WANTED"; exit 1; }
IMGID=$(docker image inspect ghcr.io/jpezzulli/sglang-rtxpro6000:v3.1.2 --format '{{.Id}}' 2>/dev/null)
[ "$IMGID" = "$PIN" ] || { log "ABORT: docker Id v3.1.2 = '${IMGID:-нет}' != пин $PIN"; exit 1; }
CURCFG=$(cat "$CFG" 2>/dev/null)
[ "$CURCFG" = "$OLD" ] || { log "ABORT: active.cfg='${CURCFG:-нет}' != $OLD"; exit 1; }
grep -qF '312' "$CFG" && { log "ABORT: cfg уже 312: живой может быть ещё 311 — или верни BAK, или systemctl restart sglang вручную"; exit 1; }
sudo -n -l 2>/dev/null | grep -q "systemctl restart sglang" || { log "ABORT: sudo -n не даёт systemctl restart sglang"; exit 1; }
log "PREFLIGHT OK: health зелёный, лист 312 валиден (md5=$SHEET_MD5), профиль md5=$PROF_MD5, Id=$IMGID, active.cfg=$OLD, sudo-набор на месте"

# --- 2) snapshot --pin (rollback-тег перед апдейтом образа — императив репо; форма из истории:
#        cd ~/penny && python3 ops/snapshot.py --label <L> --pin) ---
( cd "$P" && python3 ops/snapshot.py --label "pre312-$TS" --pin ) >>"$LOG" 2>&1 \
  || { log "ABORT: snapshot.py --pin вернул код != 0 — до свитча не идём"; exit 1; }
log "snapshot: rollback:keep-pre312-$TS готов"

# --- 3) switch active.cfg -> serve-penny-312.sh  [owner-go (3.1.2)] ---
trap 'log "ABORTED by signal — проверь systemctl status sglang; при down: reset-failed + start"; exit 129' HUP INT TERM PIPE
cp -a "$CFG" "$BAK" || { log "ABORT: не сделал бэкап $BAK"; exit 1; }
printf '%s\n' "$NEW" > "$CFG"   # та же форма записи, что исторически: echo <имя> > active.cfg (одна строка+LF)
[ "$(cat "$CFG")" = "$NEW" ] || { cp -a "$BAK" "$CFG" || log "CRITICAL: cfg НЕ восстановлен, руками: cp $BAK $CFG"; log "ABORT: read-back active.cfg != $NEW, восстановил"; exit 1; }
log "owner-go (3.1.2): switch исполнен: $CFG: $OLD -> $NEW (бэкап $BAK)"

# --- 4) restart ---
sudo -n systemctl reset-failed sglang || log "WARN: reset-failed перед restart не прошёл"
N0=$(systemctl show sglang -p NRestarts --value); N0=${N0:-0}
log "restart sglang (NRestarts before=$N0)"
sudo -n systemctl restart sglang || rollback "systemctl restart sglang вернул код != 0"
RESTART_TS=$(date '+%Y-%m-%d %H:%M:%S')
log "restart вернул, журнал с $RESTART_TS"

# --- 5) WATCH 150x10c: анкоренный OOM / is-failed / crash-loop / health ---
ok=0
for i in $(seq 1 150); do
  sleep 10
  if journalctl -u sglang --since "$RESTART_TS" --no-pager 2>/dev/null \
     | grep -qE "ValueError: Not enough host memory|torch\.cuda\.OutOfMemoryError|CUDA out of memory"; then
    log "EARLY-ABORT: OOM-маркер в журнале на итерации $i ($((i*10))s)"
    rollback "ранний OOM-маркер в журнале"
  fi
  if [ "$(systemctl is-failed sglang)" = "failed" ]; then rollback "unit is-failed"; fi
  N=$(systemctl show sglang -p NRestarts --value)
  if [ "${N:-0}" -gt "$((N0+2))" ]; then log "CRASH-LOOP: NRestarts $N0 -> $N"; rollback "crash-loop unit"; fi
  if h; then ok=1; log "HEALTH_OK ($((i*10))s)"; break; fi
done
[ "$ok" = 1 ] || rollback "UNHEALTHY 1500s"

# --- 6) ASSERTS ---
fail=0
# (a) якорь свежего старта: строка баннера профиля в журнале с RESTART_TS
if journalctl -u sglang --since "$RESTART_TS" --no-pager 2>/dev/null | grep -qF "Pennyroyal profile: Flash-Next"; then
  log "ASSERT OK (a): стартовый баннер профиля в журнале с $RESTART_TS"
else
  log "ASSERT FAIL (a): баннер 'Pennyroyal profile: Flash-Next' не найден в журнале"; fail=1
fi
# (a2) F2-ii: негатив-греп MTP policy union mismatch (валидация v3.1 печатается на старте, до health)
if journalctl -u sglang --since "$RESTART_TS" --no-pager 2>/dev/null \
   | grep -qiE "not in\b.*union|union candidates|Adaptive MTP policy missing|does not match"; then
  log "ASSERT FAIL (a2): MTP policy union mismatch в журнале за окно"; fail=1
else
  log "ASSERT OK (a2): MTP union mismatch не обнаружен"
fi
# best-effort: любая строка про "Penny Royal 3.1.2", если формат баннера изменится в 3.1.2
journalctl -u sglang --since "$RESTART_TS" --no-pager 2>/dev/null | grep -m3 "Penny Royal 3.1.2" >>"$LOG"
# (b) живой cmdline: ровно 1 процесс, поэлементно новые+spec флаги
mapfile -t PIDS < <(pgrep -f "[/]opt/pennyroyal/.venv/bin/sglang serve" || true)
if [ "${#PIDS[@]}" -ne 1 ]; then
  log "ASSERT FAIL (b): совпадений 'sglang serve': ${#PIDS[@]} (${PIDS[*]:-none})"; fail=1
else
  PID=${PIDS[0]}
  CMEL=$(tr '\0' '\n' < "/proc/$PID/cmdline")
  el(){ printf '%s\n' "$CMEL" | grep -qxF -- "$1"; }
  if el '--no-ple-offload-embedding'; then log "ASSERT OK: PLE in VRAM"; else log "ASSERT FAIL: --no-ple-offload-embedding"; fail=1; fi
  if el '--mem-fraction-static' && el '0.93'; then log "ASSERT OK: mem-fraction 0.93"; else log "ASSERT FAIL: пара --mem-fraction-static/0.93"; fail=1; fi
  if el '--hicache-size' && el '30'; then log "ASSERT OK: hicache-size 30"; else log "ASSERT FAIL: пара --hicache-size/30"; fail=1; fi
  if el '--chunked-prefill-size' && el '8192'; then log "ASSERT OK: chunked-prefill 8192"; else log "ASSERT FAIL: пара --chunked-prefill-size/8192"; fail=1; fi
  if el '--speculative-num-steps' && el '7'; then log "ASSERT OK: spec steps 7"; else log "ASSERT FAIL: пара --speculative-num-steps/7"; fail=1; fi
  if el '--speculative-num-draft-tokens' && el '8'; then log "ASSERT OK: spec draft 8"; else log "ASSERT FAIL: пара --speculative-num-draft-tokens/8"; fail=1; fi
  log "asserts (b): PID=$PID"
fi
# (c) живой контейнер на v3.1.2 + полный digest-pin (здесь же и якорь версии, см. шапку)
CI=$(docker inspect penny --format '{{.Config.Image}}' 2>/dev/null)
case "$CI" in
  *"sglang-rtxpro6000:v3.1.2@$PIN"*) log "ASSERT OK (c): Config.Image=$CI" ;;
  *) log "ASSERT FAIL (c): Config.Image='${CI:-нет}' != $IMG"; fail=1 ;;
esac
[ "$fail" = 0 ] || rollback "asserts после HEALTH_OK провалены"

# --- 7) POST-ЛОГ ---
log "пост-состояние:"
journalctl -u sglang --since "$RESTART_TS" --no-pager 2>/dev/null \
  | grep -iE "Pennyroyal profile|max_total_num_tokens|available_gpu_mem" | tail -10 >>"$LOG"
{ nvidia-smi --query-gpu=memory.used,memory.total --format=csv; free -h; } >>"$LOG" 2>&1

# --- 8) SOAK 30x10s (F1/N3): база NRestarts снимается НА ВХОДЕ в soak; ловушка активна ---
log "soak: 30x10s post-DONE наблюдение"
N0S=$(systemctl show sglang -p NRestarts --value); N0S=${N0S:-0}
for i in $(seq 1 30); do
  sleep 10
  if journalctl -u sglang --since "$RESTART_TS" --no-pager 2>/dev/null \
     | grep -qE "ValueError: Not enough host memory|torch\.cuda\.OutOfMemoryError|CUDA out of memory"; then
    rollback "soak: OOM"
  fi
  if [ "$(systemctl is-failed sglang)" = "failed" ]; then rollback "soak: unit is-failed"; fi
  N=$(systemctl show sglang -p NRestarts --value)
  if [ "${N:-0}" -gt "$N0S" ]; then rollback "soak: NRestarts +$N (база soak=$N0S)"; fi
  MA=$(awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo)
  if [ "${MA:-999}" -lt 6 ]; then rollback "soak: MemAvailable ${MA}G"; fi
  if [ $((i % 5)) -eq 0 ]; then
    log "soak $i: MA=${MA}G nvidia-used=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader | head -1)"
  fi
done
log "UPGRADE312=SOAK_OK"
trap - HUP INT TERM PIPE
log "UPGRADE312=DONE"
exit 0
