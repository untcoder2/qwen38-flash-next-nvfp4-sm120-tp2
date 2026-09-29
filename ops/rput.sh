#!/usr/bin/env bash
# rput.sh — залить файл(ы) на GPU-хост в ~/agent-tools/ (или в RPUT_DEST).
#
#   ops/rput.sh ops/_probe.py            # -> molochko@192.168.2.127:~/agent-tools/
#   RPUT_DEST=~/pentest-arena/ ops/rput.sh gen_arena.py
#
set -euo pipefail

HOST="${RPUT_HOST:-molochko@192.168.2.127}"
KEY="${RPUT_KEY:-$HOME/.ssh/molochko_deploy}"
DEST="${RPUT_DEST:-~/agent-tools/}"

if [[ $# -lt 1 ]]; then
  echo "usage: ${0##*/} <file...>" >&2
  exit 2
fi

ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=5 "$HOST" "mkdir -p $DEST"
exec scp -i "$KEY" -o BatchMode=yes -o ConnectTimeout=5 -- "$@" "$HOST:$DEST"
