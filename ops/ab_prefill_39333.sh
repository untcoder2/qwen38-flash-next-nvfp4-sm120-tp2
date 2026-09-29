#!/bin/bash
# A/B холодного префила #39333: 2.5.1 vs 2.5.2 + переход на reasoning-effort.sh.
# Два рестарта, авто-откат конфига при провале, .maintenance на всё время.
set -u
V=/home/molochko/vllm; P=/home/molochko/penny; OP=$P/ops
log(){ echo "$(date '+%T') [ab39] $*"; }
wait_health(){ for i in $(seq 1 120); do sleep 10; curl -sf -m 5 http://127.0.0.1:1234/health_generate >/dev/null && { log "HEALTH $((i*10))s"; return 0; }; done; return 1; }
restore_cfg(){ cp -a $OP/../tp2-serve-flash-next.sh.bak-effort $P/tp2-serve-flash-next.sh; }
fail(){ log "FAIL: $1 -> recovery"; restore_cfg; echo serve-penny-252.sh > $V/active.cfg; sudo -n /usr/bin/systemctl restart sglang; wait_health || log CRITICAL; rm -f $V/.maintenance; echo "=== AB39-FAILED ==="; exit 1; }

touch $V/.maintenance; log "maintenance ON"
cp -a $P/tp2-serve-flash-next.sh $OP/../tp2-serve-flash-next.sh.bak-effort
python3 $OP/apply_effort_edit.py || fail "apply_effort_edit"
grep -c 'reasoning-effort.sh' $P/tp2-serve-flash-next.sh | xargs echo "helper-lines:"

probe2(){ log "cold probe ($1):"; sleep 5; python3 $OP/prefill_probe.py; log "warm probe ($1):"; python3 $OP/prefill_probe.py; }

log "ARM 1: v2.5.1"
echo serve-penny.sh > $V/active.cfg
sudo -n /usr/bin/systemctl restart sglang; wait_health || fail "2.5.1 not up"
curl -s -m 8 http://127.0.0.1:1234/get_server_info | python3 -c 'import json,sys;d=json.load(sys.stdin);print("251 kwargs:",d.get("default_chat_template_kwargs"),"| KV:",d.get("max_total_num_tokens"))'
probe2 2.5.1

log "ARM 2: v2.5.2"
echo serve-penny-252.sh > $V/active.cfg
sudo -n /usr/bin/systemctl restart sglang; wait_health || fail "2.5.2 not after edit"
curl -s -m 8 http://127.0.0.1:1234/get_server_info | python3 -c 'import json,sys;d=json.load(sys.stdin);print("252 kwargs:",d.get("default_chat_template_kwargs"))'
probe2 2.5.2
python3 $OP/verify.py $P/snapshots/*252-live/MANIFEST.json 2>&1 | tail -8
rm -f $V/.maintenance; log "maintenance OFF"
echo "=== AB39-OK ==="
