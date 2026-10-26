#!/bin/bash
# Pennyroyal v3.1.2; имя w4 историческое — фактическая ширина в монтируемом профиле (tiered 7/8).
# Волна 3.1.2 (2026-10-26, owner-go): image-pin v3.1.1@f2a6a8ca… -> v3.1.2@013a685d…,
# всё остальное 1:1 (профиль tp2-serve-flash-next.sh не менялся: хелперы SAME, доки A/B).
# Профиль: tp2-serve-flash-next.sh (mf 0.93, hc 30, chunk 8192, PLE VRAM).
# Копия боевого serve-penny-311.sh: дельты — профиль + лог-тег; политика ширины = сток
# adaptive-next.json из образа (tp2-adaptive-w4.json НЕ монтируется), launch 7/8 из профиля.
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
  echo "$(date) serve-penny-312: замок занят, выхожу" >> /home/molochko/vllm/serve-active.log
  exit 0
fi

D=/usr/lib/x86_64-linux-gnu
NAME=penny
docker rm -f "$NAME" >/dev/null 2>&1
trap 'docker rm -f "$NAME" >/dev/null 2>&1' EXIT INT TERM

exec docker run --rm --name "$NAME" \
  --device /dev/nvidia0 --device /dev/nvidia1 --device /dev/nvidiactl \
  --device /dev/nvidia-uvm --device /dev/nvidia-uvm-tools \
  -v $D/libcuda.so.1:/nvdrv/libcuda.so.1:ro \
  -v $D/libnvidia-ml.so.1:/nvdrv/libnvidia-ml.so.1:ro \
  -v $D/libnvidia-nvvm.so.4:/nvdrv/libnvidia-nvvm.so.4:ro \
  -v $D/libnvidia-ptxjitcompiler.so.1:/nvdrv/libnvidia-ptxjitcompiler.so.1:ro \
  -v /home/molochko/penny/tp2-serve-flash-next.sh:/opt/pennyroyal/configs/pennyroyal/serve-flash-next.sh:ro \
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
  ghcr.io/jpezzulli/sglang-rtxpro6000:v3.1.2@sha256:013a685d042b178842cbf016e7deb8d23113b87044355ef785dbee6d56e57db6 next-plain
