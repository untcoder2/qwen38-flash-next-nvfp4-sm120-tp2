#!/usr/bin/env bash
# Добавить друга в WireGuard-сервер wg1 на этом хосте (инфер 192.168.2.127:1234).
#
#   sudo bash wg1-add-friend.sh <имя> [пубключ_друга]
#
# Без пубключа генерирует пару ключей сам — тогда вместе с логом выводится
# готовый клиентский конфиг, его отдать другу как файл friend-<имя>-wg1.conf
# (в нём приватный ключ — пересылать по нормальному каналу, не в общий чат).
# Свой ключ друг может сгенерить сам: wg genkey | tee privatekey | wg pubkey
# и прислать только пубключ — приватник на сервер не ложится.
#
# Друг получает доступ ТОЛЬКО к 192.168.2.127 (AllowedIPs в клиентском конфиге),
# остальной LAN в туннель не маршрутизируется.
#
# Переопределяемые env: ENDPOINT (внешний адрес для друга), PORT, IFACE,
# SUBNET_BASE, INFER_IP, OUT_DIR, CONF_DIR, FRIEND_PRIVKEY_FILE (файл с
# приватным ключом друга, если генерит не скрипт), DO_ROOT_STEPS (0 = только
# сгенерировать конфиги, без systemd/ufw; для тестов).
set -euo pipefail

NAME="${1:?usage: $0 <name> [friend_pubkey]}"
PUB="${2:-}"

ENDPOINT="${ENDPOINT:-188.255.118.241}"
PORT="${PORT:-443}"
IFACE="${IFACE:-wg1}"
SUBNET_BASE="${SUBNET_BASE:-10.9.0}"
INFER_IP="${INFER_IP:-192.168.2.127}"
OUT_DIR="${OUT_DIR:-/home/molochko/agent-tools}"
CONF_DIR="${CONF_DIR:-/etc/wireguard}"
DO_ROOT_STEPS="${DO_ROOT_STEPS:-1}"

# ENDPOINT/PORT интерполируются в клиентский профиль — не пускать переносы строк
# (инъекция произвольных ключей wg-quick, напр. AllowedIPs=0.0.0.0/0)
[[ "$ENDPOINT" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "ENDPOINT: только host/IP без пробелов" >&2; exit 2; }
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] || { echo "PORT: цифры 1-5" >&2; exit 2; }

[[ "$NAME" =~ ^[A-Za-z0-9_-]{1,15}$ ]] || { echo "имя: только латиница/цифры/-_ (1-15)" >&2; exit 2; }
[[ -d "$OUT_DIR" ]] || mkdir -p "$OUT_DIR"

CONF="$CONF_DIR/$IFACE.conf"
FNAME="friend-$NAME"; [[ "$NAME" == "friend" ]] && FNAME="friend"
CLIENT="$OUT_DIR/$FNAME-wg1.conf"

# --- ключи сервера (создаётся один раз) -------------------------------------
KEYDIR="$CONF_DIR"
if [[ ! -e "$CONF" ]]; then
    umask 077
    mkdir -p "$CONF_DIR"
    wg genkey > "$KEYDIR/.${IFACE}.server.key.tmp"
    mv "$KEYDIR/.${IFACE}.server.key.tmp" "$KEYDIR/.${IFACE}.server.key"
fi
SERVER_PRIV=$(cat "$KEYDIR/.${IFACE}.server.key")
SERVER_PUB=$(printf '%s' "$SERVER_PRIV" | wg pubkey)

# --- ключ друга --------------------------------------------------------------
PRIV_FILE="$OUT_DIR/$FNAME.key"
if [[ -n "${FRIEND_PRIVKEY_FILE:-}" ]]; then
    cp "$FRIEND_PRIVKEY_FILE" "$PRIV_FILE"; chmod 600 "$PRIV_FILE"
fi
if [[ -n "$PUB" ]]; then
    [[ "$PUB" =~ ^[A-Za-z0-9+/]{43}= ]] || { echo "пубключ должен быть 44 символа base64 из wg pubkey" >&2; exit 2; }
    [[ -e "$PRIV_FILE" ]] || echo "внимание: приватника друга нет ($PRIV_FILE) — клиентский конфиг друг соберёт сам из шаблона ниже" >&2
else
    umask 077
    if [[ ! -e "$PRIV_FILE" ]]; then
        wg genkey > "$PRIV_FILE"
    fi
    PUB=$(wg pubkey < "$PRIV_FILE")
fi

# --- какой IP отдать другу ----------------------------------------------------
# idempotency: peer с таким PUB уже есть — оставляем его IP и не дублируем блок
PEER_IP=""
NEW_CONF=""
if [[ -e "$CONF" ]]; then
    PEER_IP=$(awk -v pub="$PUB" '
        /^\[Peer\]/ {keep=0; cap=""}
        /^PublicKey/ {cur=$3}
        /^AllowedIPs/ && cur==pub {split($3,a,"/"); print a[1]; exit}
    ' "$CONF")
fi
if [[ -z "$PEER_IP" ]]; then
    for i in $(seq 2 200); do
        cand="$SUBNET_BASE.$i"
        if [[ ! -e "$CONF" ]] || ! grep -q "AllowedIPs = $cand/32" "$CONF"; then
            PEER_IP="$cand"; break
        fi
    done
    [[ -n "$PEER_IP" ]] || { echo "нет свободных адресов в $SUBNET_BASE.0/24" >&2; exit 3; }
    NEW_CONF=1
fi

# --- конфиг сервера ------------------------------------------------------------
if [[ ! -e "$CONF" ]]; then
    cat > "$CONF" <<EOF
# wg1 — персональные туннели для доступа к инферу. Пишет wg1-add-friend.sh.
# Удалить друга: убрать его [Peer]-блок и systemctl restart wg-quick@wg1.
[Interface]
Address = $SUBNET_BASE.1/24
ListenPort = $PORT
PrivateKey = $SERVER_PRIV
EOF
    chmod 600 "$CONF"
fi
if [[ -n "$NEW_CONF" ]]; then
    cat >> "$CONF" <<EOF

[Peer] # $NAME
PublicKey = $PUB
AllowedIPs = $PEER_IP/32
EOF
fi

# --- клиентский конфиг друга ----------------------------------------------------
CLIENT_CONF=$(cat <<EOF
[Interface]
PrivateKey = $( [[ -e "$PRIV_FILE" ]] && cat "$PRIV_FILE" || echo "<ПРИВАТНЫЙ КЛЮЧ ДРУГА>" )
Address = $PEER_IP/32

[Peer]
PublicKey = $SERVER_PUB
Endpoint = $ENDPOINT:$PORT
AllowedIPs = $INFER_IP/32
PersistentKeepalive = 25
EOF
)
if [[ -e "$PRIV_FILE" ]]; then
    printf '%s\n' "$CLIENT_CONF" > "$CLIENT"
    chmod 600 "$CLIENT"
    OWN=${SUDO_UID:-$(id -u)}
    chown "${SUDO_USER:-molochko}" "$CLIENT" "$PRIV_FILE" 2>/dev/null || true
fi

# --- поднять --------------------------------------------------------------------
if [[ "$DO_ROOT_STEPS" == "1" ]]; then
    if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
        ufw allow "$PORT"/udp >/dev/null && echo "ufw: разрешён UDP $PORT"
    fi
    if ! systemctl is-active --quiet wg-quick@$IFACE; then
        systemctl enable --now wg-quick@$IFACE
    else
        wg syncconf "$IFACE" <(wg-quick strip "$IFACE")
    fi
fi

echo "================= ГОТОВО ================="
echo "сервер: $IFACE $SUBNET_BASE.1/24, порт $PORT, pub $SERVER_PUB"
echo "друг '$NAME': $PEER_IP/32, pubkey $PUB"
echo "клиентский конфиг: $CLIENT"
echo "--------- отослать другу этот файл ---------"
printf '%s\n' "$CLIENT_CONF"
echo "---------- на роутере: проброс UDP $PORT -> $INFER_IP ----------"
echo "проверка после подключения друга: sudo wg show $IFACE (latest handshake)"
