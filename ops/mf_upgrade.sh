#!/bin/bash
# mem-fraction 0.90->0.92 c автооткатом; запускать на хосте.
set -u
P=/home/molochko/penny
wait_health(){ for i in $(seq 1 "$2"); do sleep 10; curl -sf -m 5 http://127.0.0.1:1234/health_generate >/dev/null && { echo "HEALTH_OK_${1}_$((i*10))s"; return 0; }; done; return 1; }
echo "$(date '+%T') restart sglang (mf=0.92)"
sudo -n /usr/bin/systemctl restart sglang
if wait_health up 30; then
  curl -s -m 8 http://127.0.0.1:1234/get_server_info | python3 -c 'import json,sys;d=json.load(sys.stdin);print("mem_fraction:",d.get("mem_fraction_static"),"| KV pool:",d.get("max_total_num_tokens"))'
else
  echo "NOT_UP -> rollback on .bak-mf090"
  cp -a $P/tp2-serve-flash-next.sh.bak-mf090 $P/tp2-serve-flash-next.sh
  sudo -n /usr/bin/systemctl restart sglang
  wait_health down 24 || echo "CRITICAL: и после откатa не поднялся"
  curl -s -m 8 http://127.0.0.1:1234/get_server_info | python3 -c 'import json,sys;d=json.load(sys.stdin);print("mem_fraction:",d.get("mem_fraction_static"))'
fi
echo "OOM-lines: $(docker logs penny 2>&1 | grep -ciE 'out of memory|CUDA error')"
echo "=== DONE ==="
