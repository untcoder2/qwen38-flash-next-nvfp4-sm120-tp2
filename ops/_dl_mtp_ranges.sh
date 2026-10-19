#!/usr/bin/env bash
# Скачивает с HF только byte-ranges MTP-тензоров FP4-Local (1.6 ГБ вместо 10).
# Запуск на хосте: ops/runsh.sh ops/_dl_mtp_ranges.sh
set -euo pipefail
# pinned revision (HF API sha на момент разведки, 15.10.2026)
REV=b9ce18a6887e18c089fdea8509a33e63429a3300
B="https://huggingface.co/nvidia/Qwen3.8-Flash-Next-FP4-Local/resolve/$REV/tensors"
D=/home/molochko/vllm-models/_fp4local_dl
mkdir -p "$D"
# headers rput'ятся в ~/agent-tools, перекладываем в рабочий каталог
for h in hdr_00001.bin hdr_00015.bin; do
  [ -f "$HOME/agent-tools/$h" ] && mv -f "$HOME/agent-tools/$h" "$D/$h"
done
ls -la "$D"
# Абсолютные байтовые диапазоны = data_offsets (относительные в safetensors!)
# + prefix (8 + длина заголовка): shard00001 prefix=1141064, shard00015 prefix=9748000.
# name shard start end (end inclusive)
rm -f "$D"/s1_scale2.bin "$D"/s1_scales.bin "$D"/s1_weights.bin "$D"/s15_mtp.bin
while read -r name sh s e; do
  out="$D/$name.bin"
  echo "GET $name bytes $s-$e"
  curl -fSL --retry 5 --retry-delay 5 --max-time 3600 -r "$s-$e" \
    "$B/model-$sh-of-00021.safetensors" -o "$out"
  have=$(stat -c%s "$out"); want=$((e - s + 1))
  [[ "$have" -eq "$want" ]] || { echo "SIZE MISMATCH $name got=$have want=$want"; exit 1; }
done <<'RANGES'
s1_scale2   00001 1144716      1150859
s1_scales   00001 2704771504   2862057903
s1_weights  00001 3742352304   5000643503
s15_mtp     00015 2769137912   2950274807
RANGES
# заголовки шардов кладёт рядом rput (hdr_00001.bin / hdr_00015.bin)
sha256sum "$D"/*.bin | tee "$D/checksums.txt"
df -h /home | tail -1; free -g | head -2
echo DL_OK
