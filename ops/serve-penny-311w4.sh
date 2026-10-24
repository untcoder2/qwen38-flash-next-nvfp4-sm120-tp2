#!/bin/bash
# Pennyroyal v3.1.1 + FIXED-W4 политика (A/B против serve-penny-311.sh): поверх
# сток-адаптивной примонтирован ops-файл tp2-adaptive-w4.json (sha ce468a5c…),
# профиль tmp/p311/tp2-serve-w4.sh (гейт ce468a5c, launch steps 3/draft 4).
# Копия боевого serve-penny-311.sh с дельтами: моунт политики + профиль + лог-тег.
# tp2-ple-backend.sh (стоковый ple-backend.sh в 3.1 default=ram — оверрай больше
# не нужен, файл на хосте сохранён для откатa); профиль tp2-serve-flash-next.sh
# перегенерирован под sha-гейт фиксированной политики 3edaa87d… (док-файлы
# tmp/p311, ревю-гейты пройдены до деплоя).
# Откат: echo serve-penny-qadmtp301.sh > ~/vllm/active.cfg && sudo systemctl restart sglang
#   и вернуть профиль: cp ~/penny/tp2-serve-flash-next.sh.bak-301sha-* ~/penny/tp2-serve-flash-next.sh
#   (внимание: qadmtp301 ждёт СТАРЫЙ профиль с sha 64070286… — откат всегда парой
#   «лист+профиль», по одному активному cfg не гадаем).
LOCK=/home/molochko/vllm/.launch.lock
exec 9>"$LOCK" || exit 1
if ! flock -n 9; then
  echo "$(date) serve-penny-311w4: замок занят, выхожу" >> /home/molochko/vllm/serve-active.log
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
  -v /home/molochko/penny/tp2-adaptive-w4.json:/opt/pennyroyal/configs/pennyroyal/adaptive-next.json:ro \
  -v /home/molochko/penny/tp2-chat-template.sh:/opt/pennyroyal/configs/pennyroyal/chat-template.sh:ro \
  -e LD_LIBRARY_PATH=/nvdrv -e CUDA_VISIBLE_DEVICES=0,1 \
  -e SGLANG_ALLOW_OVERWRITE_LONGER_CONTEXT_LEN=1 \
  -e TARGET_MODEL=/home/molochko/vllm-models/Qwen3.8-Flash-Next-NVFP4-qadmtp \
  -e SGLANG_SM120_ONLINE_MXFP8=true \
  -e PENNY_REASONING_EFFORT=xhigh \
  -e TRANSFORMERS_VERBOSITY=error \
  -v /home/molochko/vllm-models:/home/molochko/vllm-models:ro \
  -v /home/molochko/penny/tls:/tls:ro \
  -v /home/molochko/penny/cache:/cache -v /home/molochko/penny/nixl:/nixl \
  --ipc=host --ulimit memlock=-1 -p 1234:8001 \
  --no-healthcheck \
  ghcr.io/jpezzulli/sglang-rtxpro6000:v3.1.1@sha256:f2a6a8caf2d314fdcea37c2448e4548f97ab3ff201fe1e2426861347665da60b next-plain
