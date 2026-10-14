#!/usr/bin/env bash
# PLE: VRAM -> host pinned RAM (TP=2 не трогаем). Первый бут с hicache 24->4:
# KV-пул разрастается за счёт освобождённой VRAM, большой хостовый тир не нужен.
# Автократ на любом UNHEALTHY. Лог: ~/penny/ops/ple-ram.log
set -u
P=/home/molochko/penny; OP=$P/ops; CD=/home/molochko/vllm
LOG=$OP/ple-ram.log
PWFILE=$HOME/agent-tools/.sudo_pw
log(){ echo "$(date '+%m-%d %H:%M:%S') $*" | tee -a "$LOG"; }
h(){ curl -sf -m 8 --cacert $P/tls/ca.pem https://127.0.0.1:1234/health_generate >/dev/null; }
TS=$(date +%Y%m%d-%H%M%S)

cp -a $P/tp2-ple-backend.sh $P/tp2-ple-backend.sh.bak-pleram-$TS
cp -a $P/tp2-serve-flash-next.sh $P/tp2-serve-flash-next.sh.bak-pleram-$TS
log "backups: .bak-pleram-$TS"

sed -i 's/^PLE_ARGS=(--no-ple-offload-embedding)/PLE_ARGS=(--ple-offload-embedding)/' $P/tp2-ple-backend.sh
sed -i 's/^PLE_OFFLOAD_EMBEDDING=false/PLE_OFFLOAD_EMBEDDING=true/' $P/tp2-ple-backend.sh
grep -q 'PLE_ARGS=(--ple-offload-embedding)' $P/tp2-ple-backend.sh || { log "ABORT: ple sed"; exit 1; }
sed -i 's/--hicache-size 24/--hicache-size 4/' $P/tp2-serve-flash-next.sh
grep -q -- '--hicache-size 4' $P/tp2-serve-flash-next.sh || { log "ABORT: hicache sed"; exit 1; }
log "edits OK: PLE->CPU pinned, hicache 24->4, TP=2 без изменений"

[ -r "$PWFILE" ] || { log "ABORT: нет $PWFILE"; exit 1; }
log "free before drop:"; free -h >>$LOG
S=$(cat "$PWFILE"); printf '%s' "$S" | sudo -S -p "" bash -c 'sync; echo 3 > /proc/sys/vm/drop_caches' >>$LOG 2>&1 || log "WARN: drop_caches не прошёл"
unset S; sleep 2; log "free after drop:"; free -h >>$LOG

log "restart sglang"
sudo -n systemctl restart sglang
ok=0
for i in $(seq 1 150); do sleep 10; if h; then ok=1; log "HEALTH_OK ($((i*10))s)"; break; fi; done
if [ $ok -eq 0 ]; then
  log "UNHEALTHY 1500s — ОТКАТ"
  sudo -n systemctl stop sglang
  cp -a $P/tp2-ple-backend.sh.bak-pleram-$TS $P/tp2-ple-backend.sh
  cp -a $P/tp2-serve-flash-next.sh.bak-pleram-$TS $P/tp2-serve-flash-next.sh
  sudo -n systemctl start sglang
  for i in $(seq 1 120); do sleep 10; h && { log "ROLLBACK HEALTH_OK"; break; }; done
  log "PLE_RAM_SWITCH=ROLLED_BACK"
  exit 2
fi

log "пост-состояние:"
{ free -h; nvidia-smi --query-gpu=memory.used,memory.total --format=csv; } >>$LOG
sudo -n journalctl -u sglang --since "-25 min" --no-pager 2>/dev/null \
  | grep -iE "KV pool|max_total_num_tokens|out of memory|Not enough|error" | tail -12 >>$LOG
log "PLE_RAM_SWITCH=DONE"
