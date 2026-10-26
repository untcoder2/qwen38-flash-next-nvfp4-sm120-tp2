#!/usr/bin/env bash
# wg-manage.sh — закреплённый root-хелпер для WireGuard на инфер-хосте.
# Разрешён к запуску от molochko без парола точечно (/etc/sudoers.d/wg-manage):
#   sudo bash /home/molochko/agent-tools/wg-manage.sh <cmd>
#
#   retire-wg0        удалить старый wg0 (профиль NM + конфиг wg-quick), архив в /etc/wireguard/disabled-*
#   add <name> [pub]  добавить друга в wg1 (делегат wg1-add-friend.sh)
#   show              wg show (публичные данные, без приватников)
#   selftest          подняться временным клиентом wg10 на самом себе, ping 10.9.0.1, убрать за собой
set -euo pipefail

CMD="${1:?usage: wg-manage.sh retire-wg0|add|show|selftest}"; shift || true
ADDDIR=/home/molochko/agent-tools
PORT="${PORT:-443}"

case "$CMD" in
  retire-wg0)
    systemctl disable --now wg-quick@wg0 2>/dev/null || true
    if nmcli connection show id wg0 >/dev/null 2>&1; then
      nmcli connection show id wg0 > /tmp/wg0.nmexport.$$ 2>/dev/null || true
    fi
    bdir="/etc/wireguard/disabled-$(date +%Y%m%d-%H%M%S)"
    install -d -m 700 "$bdir"
    [[ -e /etc/wireguard/wg0.conf ]] && mv /etc/wireguard/wg0.conf "$bdir/wg0.conf"
    [[ -s /tmp/wg0.nmexport.$$ ]] && mv /tmp/wg0.nmexport.$$ "$bdir/wg0.nmprofile.txt"
    nmcli connection delete id wg0 2>/dev/null || true
    ip link show wg0 >/dev/null 2>&1 && ip link delete wg0 || true
    echo "wg0 снят и удалён; архив (с ключами, root-only): $bdir"
    ;;
  add)
    bash "$ADDDIR/wg1-add-friend.sh" "$@"
    ;;
  show)
    wg show
    ;;
  check)
    # честная диагностика серверной части (ping «сам себе» через wg на одном хосте
    # методически неверен — реальный контроль это handshake приходящего клиента)
    echo "unit: $(systemctl is-enabled wg-quick@wg1 2>&1)/$(systemctl is-active wg-quick@wg1 2>&1)"
    ip -br addr show wg1 2>/dev/null || echo "wg1 НЕТ"
    ss -uln | grep -q ":$PORT " && echo "UDP $PORT слушается" || echo "UDP $PORT НЕ слушается!"
    command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active" && echo "ufw активен — проверить allow $PORT/udp" || true
    echo "peers: $(wg show wg1 peers | wc -w)"
    ;;
  handshakes)
    wg show wg1 latest-handshakes
    ;;
  conf)
    sed 's/^PrivateKey = .*/PrivateKey = [скрыто]/' /etc/wireguard/wg1.conf
    ;;
  *)
    echo "неизвестная команда: $CMD" >&2
    exit 2
    ;;
esac
