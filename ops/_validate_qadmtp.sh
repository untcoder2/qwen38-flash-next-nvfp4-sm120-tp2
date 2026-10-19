#!/usr/bin/env bash
set -uo pipefail
T0=$(date +%s)
echo "=== SHORT_BENCH"
python3 ~/agent-tools/short_bench.py
echo "=== DEGEN"
DEGEN_URL=https://127.0.0.1:1234/v1/chat/completions \
  SSL_CERT_FILE=/home/molochko/penny/tls/ca.pem \
  timeout 600 python3 ~/vllm/_degen.py 2>&1 | tail -18
echo "=== ACCEPT_WINDOW"
docker logs penny --since "$T0" 2>&1 |
  grep -oE 'accept len: [0-9.]+' |
  sed 's/accept len: //' > /tmp/accept_qad.txt
python3 - <<'EOF'
import statistics
v=[float(x) for x in open('/tmp/accept_qad.txt') if x.strip()]
if v: print(f"n={len(v)} median={statistics.median(v):.2f} mean={statistics.mean(v):.2f} p10={sorted(v)[len(v)//10]:.2f}")
else: print("no accept samples")
EOF
echo VAL_DONE
