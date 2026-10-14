#!/usr/bin/env bash
# rsh.sh — выполнить команду на GPU-хосте (molochko@192.168.2.127).
#
#   ops/rsh.sh 'nmap -sV 10.0.0.5'
#   ops/rsh.sh 'python3 ~/agent-tools/_probe.py'
#   ops/rsh.sh 'ffuf -u http://target/FUZZ -w ~/wordlists/common.txt -o out.json -of json'
#
# Команда выполняется удалённым bash -lc (login-окружение, PATH из .profile).
# Exit-код хостовой команды пробрасывается наружу. Только неинтерактив:
# ключ BatchMode, пароли не вводятся. Переменные RSH_HOST / RSH_KEY переопределяют
# цель, если нужно на другой хост или другой ключ.
set -euo pipefail

HOST="${RSH_HOST:-molochko@192.168.2.127}"
KEY="${RSH_KEY:-$HOME/.ssh/molochko_deploy}"

if [[ $# -lt 1 ]]; then
  echo "usage: ${0##*/} '<command>'" >&2
  exit 2
fi

cmd=$(printf '%q ' "$@")
exec ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=5 "$HOST" bash -lc "$cmd"
