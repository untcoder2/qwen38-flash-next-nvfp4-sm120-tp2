#!/bin/bash
# Minimal repro for Pennyroyal v3.0.2 adaptive-spec switch thrash (upstream issue).
# Run on the host serving the model; prints switch-event rate and greedy medians.
IMG=ghcr.io/jpezzulli/sglang-rtxpro6000:v3.0.2   # same launch flags as production profile
# Preconditions: container up with --speculative-adaptive + bundled adaptive-next.json,
# server otherwise idle. Then:

# 1) one greedy request, 512 tokens, temp=0
t0=$(date +%s%N)
curl -sk https://localhost:1234/v1/completions \
  -H 'content-type: application/json' \
  -d '{"model":"default","prompt":"The quick brown fox jumps over the lazy dog. Repeat this sentence until 512 tokens.","max_tokens":512,"temperature":0}' \
  -o /dev/null
echo "elapsed_ms=$(( ($(date +%s%N)-t0)/1000000 ))"

# 2) count controller events produced by that single request:
docker logs --since 1m <container> 2>&1 | grep -cE "Switch adaptive runtime state"
# Expected: 0 (steady accept ~4.5 pins the wide state after at most one settle).
# Observed on v3.0.2: several, including same-second 7->3->7 reversals.

# 3) repeat step 1 five times, compute median elapsed -> bimodal on v3.0.2
#    (runs caught mid-thrash ~30% slower), tight around ~412 tok/s on v3.0.1.
