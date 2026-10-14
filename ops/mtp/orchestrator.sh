#!/bin/bash
# Хостовый оркестратор тренировки MTP-головы. Запускать как:
#   nohup bash ~/mtp-work/orchestrator.sh > ~/mtp-work/orch.log 2>&1 &
# Прод: стоп на всё время, гарантированный рестарт даже при провале (trap).
set -uo pipefail
W=/home/molochko/mtp-work
IMG=ghcr.io/jpezzulli/sglang-rtxpro6000:v3.0.1
D=/usr/lib/x86_64-linux-gnu
MODELDIR=/home/molochko/vllm-models/flash-next-mtp-r1
SHEET=/home/molochko/penny/tp2-serve-flash-next.sh
export W IMG MODELDIR SHEET
S(){ echo "[$(date -Is)] $*" >> $W/STATUS; }
PROD_DOWN=0; KEEP_FT=0
cleanup(){
  local rc=$?
  docker rm -f penny-mtp >/dev/null 2>&1
  if [ "$KEEP_FT" = 0 ] && [ -f $W/sheet-before.sh ]; then cp $W/sheet-before.sh "$SHEET" 2>/dev/null; fi
  rm -f /home/molochko/vllm/.maintenance
  if [ "$PROD_DOWN" = 1 ]; then sudo -n systemctl start sglang >>$W/orch.log 2>&1 || S "!!! НЕ УДАЛОСЬ ПОДНЯТЬ ПРОД ВРУЧНУЮ"; fi
  [ $rc -ne 0 ] && S "ORCHESTRATOR-FAILED rc=$rc (прод восстановлен на конфиге до тренировки)"
}
trap cleanup EXIT

mkdir -p $W/out
[ -s $W/corpus.jsonl ] || { S "FATAL нет corpus.jsonl"; exit 1; }
S "clone SpecForge"
[ -d $W/SpecForge/.git ] || git clone --depth 1 https://github.com/sgl-project/SpecForge $W/SpecForge >>$W/orch.log 2>&1 || { S "FATAL git clone"; exit 2; }
tar xzf $W/ext.tar.gz -C $W/SpecForge/ || { S "FATAL ext tar"; exit 3; }
if [ -d $W/SpecForge/specforge-ext ]; then rm -rf $W/SpecForge/specforge_ext && mv $W/SpecForge/specforge-ext $W/SpecForge/specforge_ext; fi
rm -rf $W/SpecForge/specforge_ext/.upstream $W/SpecForge/specforge_ext/__pycache__
[ -f $W/SpecForge/specforge_ext/selftest.py ] || { S "FATAL нет selftest.py после распаковки"; exit 3; }
cp $W/run-mtp-hc.yaml $W/SpecForge/run-mtp-hc.yaml
cp $W/sheet.ft.sh "$SHEET" || { S "FATAL sheet.ft"; exit 4; }

S "stop prod (время простоя пошло)"
touch /home/molochko/vllm/.maintenance
sudo -n systemctl stop sglang || { S "FATAL sudo stop"; exit 5; }
PROD_DOWN=1
docker rm -f penny >/dev/null 2>&1
sleep 5
nvidia-smi --query-gpu=memory.used --format=csv,noheader >>$W/STATUS

S "container: capture+train+merge старт"
docker run --rm --name penny-mtp \
  --device /dev/nvidia0 --device /dev/nvidia1 --device /dev/nvidiactl \
  --device /dev/nvidia-uvm --device /dev/nvidia-uvm-tools \
  -v $D/libcuda.so.595.84:/nvdrv/libcuda.so.1:ro \
  -v $D/libnvidia-ml.so.595.84:/nvdrv/libnvidia-ml.so.1:ro \
  -v $D/libnvidia-nvvm.so.595.84:/nvdrv/libnvidia-nvvm.so.4:ro \
  -v $D/libnvidia-ptxjitcompiler.so.595.84:/nvdrv/libnvidia-ptxjitcompiler.so.1:ro \
  -e LD_LIBRARY_PATH=/nvdrv -e CUDA_VISIBLE_DEVICES=0,1 \
  -e SGLANG_ALLOW_OVERWRITE_LONGER_CONTEXT_LEN=1 -e TRANSFORMERS_VERBOSITY=error \
  -v /home/molochko/vllm-models:/home/molochko/vllm-models:ro \
  -v $W:/work -v /home/molochko/penny/cache:/cache \
  --entrypoint bash "$IMG" /work/run_in_container.sh >>$W/orch.log 2>&1
CRC=$?
S "container rc=$CRC"
[ $CRC -ne 0 ] && { tail -20 $W/steps.log >>$W/STATUS 2>/dev/null; exit 6; }

S "установка головы r1"
[ -d $W/out/flash-next-mtp-r1 ] || { S "FATAL нет out/mtp-r1"; exit 7; }
rm -rf "$MODELDIR"; cp -r $W/out/flash-next-mtp-r1 "$MODELDIR"
S "start prod на новой голове"
sudo -n systemctl start sglang
for i in $(seq 1 60); do sleep 10; code=$(curl -s -m 5 -o /dev/null -w "%{http_code}" http://127.0.0.1:1234/health 2>/dev/null); [ "$code" = 200 ] && break; done
[ "$code" = 200 ] || { S "FATAL прод не поднялся за 10 мин — ОТКАТ"; cp $W/sheet-before.sh "$SHEET"; SWAPPED=0; sudo -n systemctl restart sglang; exit 8; }
S "prod healthy"

sleep 20
cd /home/molochko/penny && S "snapshot ft-r1" && python3 ops/snapshot.py --label ft-r1 --pin >>$W/orch.log 2>&1
S "verify ft-r1"
python3 ops/verify.py $(ls -d /home/molochko/penny/snapshots/*ft-r1/MANIFEST.json | tail -1) >>$W/STATUS 2>&1 && S "VERIFY-PASS" || {
  S "VERIFY-FAIL — откат на лист без ft"; cp $W/sheet-before.sh "$SHEET"; sudo -n systemctl restart sglang; exit 9; }
S "spec_diag post: bs1+long"
python3 ops/spec_diag.py --mode bs1 --out /home/molochko/agent-tools/spec_diag_ft.jsonl --budget 14 >>$W/orch.log 2>&1
python3 ops/spec_diag.py --mode long --out /home/molochko/agent-tools/spec_diag_ft.jsonl >>$W/orch.log 2>&1
python3 ops/spec_diag.py --mode report --out /home/molochko/agent-tools/spec_diag_ft.jsonl >>$W/STATUS 2>&1
python3 ops/short_bench.py >>$W/STATUS 2>&1
docker rm -f penny-mtp >/dev/null 2>&1
rm -f /home/molochko/vllm/.maintenance
KEEP_FT=1; trap - EXIT
S "ORCHESTRATOR-DONE (head=ft-r1, v3.0.1)"
