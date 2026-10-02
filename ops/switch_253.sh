#!/bin/bash
# Свитч стенда на v2.5.3 с автооткатом на v2.5.2. Только установка + мин-самопроверка.
set -u
V=/home/molochko/vllm
log(){ echo "$(date '+%T') [253] $*"; }
wait_health(){ for i in $(seq 1 120); do sleep 10; curl -sf -m 5 http://127.0.0.1:1234/health_generate >/dev/null && { log "HEALTH_OK $((i*10))s"; return 0; }; [ $((i%6)) -eq 0 ] && log "waiting ${i}0s..."; done; return 1; }
smoke(){ python3 - <<'PY'
import json,urllib.request,sys
b={"model":"qwen3.8-flash-next","messages":[{"role":"user","content":"Ответь одним словом: ок"}],"max_tokens":24,"temperature":0}
r=urllib.request.Request("http://127.0.0.1:1234/v1/chat/completions",data=json.dumps(b).encode(),headers={"Content-Type":"application/json"})
try:
    j=json.loads(urllib.request.urlopen(r,timeout=120).read()); m=j["choices"][0]["message"]
    ok = bool(m.get("content") or m.get("reasoning_content")); print("smoke:", "OK" if ok else "EMPTY"); sys.exit(0 if ok else 1)
except Exception as e:
    print("SMOKE-FAIL:",e,file=sys.stderr); sys.exit(1)
PY
}
rollback(){ log "ROLLBACK -> serve-penny-252.sh"; echo serve-penny-252.sh > $V/active.cfg; sudo -n /usr/bin/systemctl restart sglang; wait_health || log "CRITICAL: и после откатa не поднялся"; }

[ "$(cat $V/active.cfg)" = serve-penny-252.sh ] || { log "ABORT: активен не serve-penny-252.sh"; exit 1; }
touch $V/.maintenance; log "maintenance ON"
echo serve-penny-253.sh > $V/active.cfg
log "restart -> v2.5.3"
sudo -n /usr/bin/systemctl restart sglang
if wait_health && smoke; then
  curl -s -m 8 http://127.0.0.1:1234/get_server_info | python3 -c 'import json,sys;d=json.load(sys.stdin);print("image-check: mf",d.get("mem_fraction_static"),"| ctx",d.get("context_length"),"| KV",d.get("max_total_num_tokens"),"| hicache",d.get("enable_hierarchical_cache"))'
  docker inspect penny --format 'image={{.Config.Image}}'
  docker exec penny env | grep -c TRANSFORMERS_VERBOSITY | xargs echo "TRV-in-container:"
  docker logs penny 2>&1 | grep -ciE "out of memory|CUDA error" | xargs echo "OOM-lines:"
  rm -f $V/.maintenance; log "maintenance OFF — 2.5.3 LIVE"
  echo "=== SWITCH-OK ==="
else
  log "FAILED"
  rollback
  rm -f $V/.maintenance
  echo "=== SWITCH-ROLLBACK ==="
fi
