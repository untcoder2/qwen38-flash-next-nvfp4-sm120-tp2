#!/bin/bash
# pumpnet-guard.sh — держит членство penny в сети pumpnet (upstream-сеть zcode-pump)
# после ЛЮБОГО рестарта sglang: лист-скрипты serve-*.sh пересоздают контейнер
# дефолтным docker run без --network, и penny приходит только на bridge —
# фронт :8744 висит на 502, пока кто-то вручную не сделает network connect
# (док docs/zcode-bridge.md, «жизненный цикл»). Сторож закрывает это сам,
# на любом активном листе и при любом пути старта (systemd, watch-vllm.sh).
# Идемпотентен: действует только когда penny запущен и его ещё нет в pumpnet.
# Раскатка: убрать cron-строку (crontab -e) и файл; серверам это безвредно.
NET=pumpnet
NAME=penny
docker ps -q -f name="^${NAME}$" | grep -q . || exit 0
if docker network inspect "$NET" --format '{{range .Containers}}{{.Name}}
{{end}}' 2>/dev/null | grep -qx "$NAME"; then exit 0; fi
docker network connect "$NET" "$NAME" 2>/dev/null && echo "$(date) pumpnet-guard: ${NAME} -> ${NET}"
