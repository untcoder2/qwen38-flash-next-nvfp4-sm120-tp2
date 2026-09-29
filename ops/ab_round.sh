#!/bin/bash
# A/B раунд: канон <-> ABLITERATED, с автооткатом на канон при любом сбое.
set -u
CD=/home/molochko/vllm
OP=/home/molochko/penny/ops
SNAP=/home/molochko/penny/snapshots
LOG=$OP/ab-run.log
CANON_CFG=serve-penny.sh
ABL_CFG=serve-fn-abliterated-penny.sh

log(){ echo "$(date '+%F %T') $*" | tee -a "$LOG"; }
health_wait(){
  for i in $(seq 1 120); do
    sleep 10
    if curl -sf -m 8 http://127.0.0.1:1234/health_generate >/dev/null; then log "HEALTH_OK ($((i*10))s)"; return 0; fi
    [ $((i % 6)) -eq 0 ] && log "waiting health ${i}0s..."
  done
  return 1
}
smoke(){
  python3 - <<'PY'
import json,urllib.request,sys
b={"model":"qwen3.8-flash-next","messages":[{"role":"user","content":"Скажи одно слово: ок"}],"max_tokens":24,"temperature":0,
   "chat_template_kwargs":{"reasoning_effort":"medium"}}
r=urllib.request.Request("http://127.0.0.1:1234/v1/chat/completions",data=json.dumps(b).encode(),headers={"Content-Type":"application/json"})
try:
    j=json.loads(urllib.request.urlopen(r,timeout=120).read())
    c=j["choices"][0]["message"]["content"]
    sys.exit(0 if (c or j["choices"][0]["message"].get("reasoning_content")) else 1)
except Exception as e:
    print("SMOKE-FAIL:",e,file=sys.stderr); sys.exit(1)
PY
}
restore_canon(){
  log "RESTORE -> $CANON_CFG"
  echo "$CANON_CFG" > $CD/active.cfg
  sudo -n /usr/bin/systemctl restart sglang
  health_wait || log "CRITICAL: канон не поднялся!"
}

log "=== AB ROUND START; active=$(cat $CD/active.cfg)"
[ "$(cat $CD/active.cfg)" = "$CANON_CFG" ] || { log "ABORT: активен не канон"; exit 1; }

# лист абилишейтеда = точный sed-клон боевого serve-penny.sh
sed "s|TARGET_MODEL=/home/molochko/vllm-models/Qwen3.8-Flash-Next-NVFP4-bf16mtp|TARGET_MODEL=/home/molochko/vllm-models/Qwen3.8-Flash-Next-ABLITERATED-NVFP4|" \
    $CD/serve-penny.sh > $CD/$ABL_CFG
chmod +x $CD/$ABL_CFG
grep -q ABLITERATED $CD/$ABL_CFG || { log "ABORT: клон листа не сложился"; exit 1; }

echo "$ABL_CFG" > $CD/active.cfg
log "switch -> $ABL_CFG; restart"
sudo -n /usr/bin/systemctl restart sglang
if ! health_wait; then log "abliterated UNHEALTHY"; restore_canon; exit 2; fi
if ! smoke; then log "abliterated SMOKE FAIL"; restore_canon; exit 3; fi

log "suite abliterated..."
python3 $OP/ab_compare.py --tag abliterated --out $OP/ab-abliterated.json 2>&1 | tee -a "$LOG"
S1=$?

restore_canon
if [ $S1 -ne 0 ]; then log "раунд прерван ошибкой харнеса"; exit 4; fi

MAN=$(ls -t $SNAP/*/MANIFEST.json $SNAP/*/manifest.json 2>/dev/null | head -1)
if [ -n "$MAN" ]; then log "verify: $MAN"; python3 $OP/verify.py "$MAN" 2>&1 | tail -8 | tee -a "$LOG"; fi

log "suite canonical..."
python3 $OP/ab_compare.py --tag canonical --out $OP/ab-canonical.json 2>&1 | tee -a "$LOG"

python3 $OP/ab_compare.py --compare $OP/ab-abliterated.json $OP/ab-canonical.json | tee $OP/ab-compare.txt
log "=== AB ROUND DONE"
