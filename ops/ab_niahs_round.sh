#!/bin/bash
# Мини-раунд: только NIAHS на ABLITERATED, с автооткатом.
set -u
CD=/home/molochko/vllm; OP=/home/molochko/penny/ops; LOG=$OP/ab-run.log
log(){ echo "$(date '+%F %T') [niahs-round] $*" | tee -a "$LOG"; }
health_wait(){ for i in $(seq 1 120); do sleep 10; curl -sf -m 8 http://127.0.0.1:1234/health_generate >/dev/null && { log "HEALTH_OK ($((i*10))s)"; return 0; }; done; return 1; }
restore(){ echo serve-penny.sh > $CD/active.cfg; sudo -n /usr/bin/systemctl restart sglang; health_wait || log "CRITICAL: канон не поднялся"; }
[ "$(cat $CD/active.cfg)" = serve-penny.sh ] || { log "ABORT: активен не канон"; exit 1; }
log "switch -> abliterated"
echo serve-fn-abliterated-penny.sh > $CD/active.cfg
sudo -n /usr/bin/systemctl restart sglang
health_wait || { log "UNHEALTHY"; restore; exit 2; }
python3 $OP/ab_compare.py --tag abliterated --out $OP/ab-abliterated.json --only niahs 2>&1 | tee -a "$LOG"
R=$?
restore
log "=== NIAHS MINI-ROUND DONE rc=$R"
