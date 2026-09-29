#!/usr/bin/env bash
# rget.sh — забрать файл/отчёт с GPU-хоста.
#
#   ops/rget.sh /home/molochko/agent-tools/out.json          # кладёт в текущий каталог
#   ops/rget.sh /home/molochko/vllm/serve-penny.sh ops/      # в ops/
#
set -euo pipefail

HOST="${RGET_HOST:-molochko@192.168.2.127}"
KEY="${RGET_KEY:-$HOME/.ssh/molochko_deploy}"

if [[ $# -lt 1 ]]; then
  echo "usage: ${0##*/} <remote-path> [local-dir=.]" >&2
  exit 2
fi
remote=$1
localdir=${2:-.}

exec scp -i "$KEY" -o BatchMode=yes -o ConnectTimeout=5 -- "$HOST:$remote" "$localdir"
