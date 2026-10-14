#!/bin/bash
# Pennyroyal v3.0.1 в контейнере, TP=2, PLE в VRAM, online MXFP8.
# Копия serve-penny-253.sh, дельта только в теге образа (контракт отката: v2.5.3
# зафиксирован digest'ом в rollback:keep и остаётся в репо образов).
# Откат: echo serve-penny-253.sh > /home/molochko/vllm/active.cfg && sudo systemctl restart sglang
LOCK=/home/molochko/vllm/.launch.lock
exec 9>"$LOCK" || exit 1
if ! flock -n 9; then
  echo "$(date) serve-penny-301: замок занят, выхожу" >> /home/molochko/vllm/serve-active.log
  exit 0
fi

D=/usr/lib/x86_64-linux-gnu
NAME=penny
docker rm -f "$NAME" >/dev/null 2>&1
trap 'docker rm -f "$NAME" >/dev/null 2>&1' EXIT INT TERM

exec docker run --rm --name "$NAME" \
  --device /dev/nvidia0 --device /dev/nvidia1 --device /dev/nvidiactl \
  --device /dev/nvidia-uvm --device /dev/nvidia-uvm-tools \
  -v $D/libcuda.so.595.84:/nvdrv/libcuda.so.1:ro \
  -v $D/libnvidia-ml.so.595.84:/nvdrv/libnvidia-ml.so.1:ro \
  -v $D/libnvidia-nvvm.so.595.84:/nvdrv/libnvidia-nvvm.so.4:ro \
  -v $D/libnvidia-ptxjitcompiler.so.595.84:/nvdrv/libnvidia-ptxjitcompiler.so.1:ro \
  -v /home/molochko/penny/tp2-serve-flash-next.sh:/opt/pennyroyal/configs/pennyroyal/serve-flash-next.sh:ro \
  -v /home/molochko/penny/tp2-ple-backend.sh:/opt/pennyroyal/configs/pennyroyal/ple-backend.sh:ro \
  -v /home/molochko/penny/tp2-chat-template.sh:/opt/pennyroyal/configs/pennyroyal/chat-template.sh:ro \
  -e LD_LIBRARY_PATH=/nvdrv -e CUDA_VISIBLE_DEVICES=0,1 \
  -e SGLANG_ALLOW_OVERWRITE_LONGER_CONTEXT_LEN=1 \
  -e TARGET_MODEL=/home/molochko/vllm-models/Qwen3.8-Flash-Next-NVFP4-bf16mtp \
  -e SGLANG_SM120_ONLINE_MXFP8=true \
  -e TRANSFORMERS_VERBOSITY=error \
  -v /home/molochko/vllm-models:/home/molochko/vllm-models:ro \
  -v /home/molochko/penny/cache:/cache -v /home/molochko/penny/nixl:/nixl \
  --ipc=host --ulimit memlock=-1 -p 1234:8001 \
  ghcr.io/jpezzulli/sglang-rtxpro6000:v3.0.1 next-plain
