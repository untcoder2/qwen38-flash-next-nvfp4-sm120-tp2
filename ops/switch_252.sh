#!/bin/bash
# Switch stand to pennyroyal v2.5.2 leaf, with full auto-rollback to v2.5.1.
set -u
V=/home/molochko/vllm
log(){ echo "$(date '+%T') [252] $*"; }
wait_health(){ for i in $(seq 1 120); do sleep 10; curl -sf -m 5 http://127.0.0.1:1234/health_generate >/dev/null && { log "HEALTH_OK $((i*10))s"; return 0; }; [ $((i%6)) -eq 0 ] && log "waiting ${i}0s..."; done; return 1; }
smoke(){ python3 - <<'PY'
import json,urllib.request,sys
b={"model":"qwen3.8-flash-next","messages":[{"role":"user","content":"Ответь одним словом: ок"}],"max_tokens":24,"temperature":0}
r=urllib.request.Request("http://127.0.0.1:1234/v1/chat/completions",data=json.dumps(b).encode(),headers={"Content-Type":"application/json"})
try:
    j=json.loads(urllib.request.urlopen(r,timeout=120).read()); m=j["choices"][0]["message"]
    sys.exit(0 if (m.get("content") or m.get("reasoning_content")) else 1)
except Exception as e: print("SMOKE-FAIL",e,file=sys.stderr); sys.exit(1)
PY
}
touch $V/.maintenance; log "maintenance ON"
echo serve-penny-252.sh > $V/active.cfg
sudo -n /usr/bin/systemctl restart sglang
if wait_health && smoke; then
  curl -s -m 8 http://127.0.0.1:1234/get_server_info | python3 -c 'import json,sys;d=json.load(sys.stdin);print("mf:",d.get("mem_fraction_static"),"| ctx:",d.get("context_length"),"| KV:",d.get("max_total_num_tokens"))'
  docker inspect penny --format 'image={{.Config.Image}}'
  rm -f $V/.maintenance; log "maintenance OFF — 2.5.2 LIVE"
  echo "=== SWITCH-OK ==="
else
  log "FAILED -> auto-rollback to serve-penny.sh (v2.5.1)"
  echo serve-penny.sh > $V/active.cfg
  sudo -n /usr/bin/systemctl restart sglang
  wait_health && log "rollback healthy (v2.5.1)" || log "CRITICAL: not up after rollback"
  rm -f $V/.maintenance
  echo "=== SWITCH-ROLLBACK ==="
fi
docker logs penny 2>&1 | grep -ciE "out of memory|CUDA error|Xid" | xargs echo "err-lines:"
