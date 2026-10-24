#!/usr/bin/env bash
# A/B холодного ре-префилла: chunked_prefill_size x mem_fraction_static.
# Волны: 4096/0.95 (база), 8192/0.95, 16384/0.93, 32768/0.90.
# Перед каждой волной — рестарт (кеш холодный = честный рефилл), после всех —
# откат профиля на базу. Дисциплина как в _run_w4_bench.sh: идентичность стенда,
# уникальность --field (урок дубля 24.10), .maintenance на всё время серии.
set -uo pipefail
root=/home/molochko/agent-tools
prof=/home/molochko/penny/tp2-serve-flash-next.sh
bak=$prof.bak-chunkab
out=$root/reports/chunkab-$(date +%Y%m%d-%H%M)
mkdir -p "$out"
cp -n "$prof" "$bak" || true
[[ -e $root/bench_suite.py && -e $root/bench_prompts.json ]] || { echo NO_BENCH; exit 1; }
touch /home/molochko/vllm/.maintenance
variants=("4096 0.95" "8192 0.95" "16384 0.93" "32768 0.90")

health() { # 25 мин максимум; при неуспехе — хвост журнала наружу
  for _ in $(seq 75); do
    sleep 20
    curl -skf -m5 https://127.0.0.1:1234/health -o /dev/null && return 0
  done
  journalctl -u sglang --no-pager 2>/dev/null | tail -20
  return 1
}

restore() { cp "$bak" "$prof"; }

for v in "${variants[@]}"; do
  read -r chunk mf <<<"$v"
  echo "== wave chunk=$chunk mem=$mf $(date -Is)"
  sed -i "s/^PREFILL_CHUNK_SIZE=.*/PREFILL_CHUNK_SIZE=$chunk/; \
          s/--mem-fraction-static [0-9.]*/--mem-fraction-static $mf/" "$prof"
  dup=$(grep -o -- '--field "[a-z_]*' "$prof" | sort | uniq -d)
  if [[ -n $dup ]]; then echo "DUP FIELDS: $dup — abort"; break; fi
  grep -cE "^PREFILL_CHUNK_SIZE=$chunk\$" "$prof" >/dev/null || { echo "sed miss chunk"; break; }
  grep -c -- "--mem-fraction-static $mf" "$prof" >/dev/null || { echo "sed miss mf"; break; }
  sudo -n systemctl restart sglang || { echo RESTART_FAIL; break; }
  health || { echo "HEALTH_FAIL on $v — rollback"; restore; sudo -n systemctl restart sglang; break; }
  ( for _ in $(seq 2400); do
      nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits
      sleep 1
    done >"$out/mem-$chunk-$mf.csv" ) & sp=$!
  python3 -I "$root/bench_suite.py" --label "chunk$chunk-mf$mf" \
    --reps 2 --sets short,long --out-dir "$out" \
    --meta "refill A/B wave: prefill_chunk=$chunk mem_frac=$mf" \
    >"$out/bench-$chunk-$mf.log" 2>&1
  rc=$?
  peak=$(sort -n "$out/mem-$chunk-$mf.csv" | tail -1)
  kill $sp 2>/dev/null
  echo "== wave $v rc=$rc peak_mib=$peak $(date -Is)"
  [[ $rc -ne 0 ]] && { echo BENCH_FAIL; break; }
done

restore
sudo -n systemctl restart sglang && health && echo BASE_RESTORED
rm -f /home/molochko/vllm/.maintenance
echo DONE=$(date -Is) out=$out
