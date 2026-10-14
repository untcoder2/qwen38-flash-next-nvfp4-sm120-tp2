#!/usr/bin/env bash
# runsh.sh — выполнить ЛОКАЛЬНЫЙ скрипт на GPU-хосте одним round-trip (bash -s).
#
#   ops/runsh.sh ./job.sh [arg1 arg2 ...]
#
# Тело скрипта уходит на хост в том же ssh-соединении, исполняется удалённым
# bash -l -s, аргументы после имени скрипта доступны как $1.., exit-код
# пробрасывается. Заменяет серии одиночных rsh.sh (меньше стартов ssh.exe —
# см. notes 14.10 про ControlMaster: транспорт не батлнек, батлнек — N вызовов).
# RSH_HOST / RSH_KEY — как в rsh.sh.
set -euo pipefail

HOST="${RSH_HOST:-molochko@192.168.2.127}"
KEY="${RSH_KEY:-$HOME/.ssh/molochko_deploy}"

if [[ $# -lt 1 || ! -r "$1" ]]; then
  echo "usage: ${0##*/} <script-file> [args...]" >&2
  exit 2
fi

script=$1; shift
exec ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=5 "$HOST" bash -l -s -- "$@" < "$script"
