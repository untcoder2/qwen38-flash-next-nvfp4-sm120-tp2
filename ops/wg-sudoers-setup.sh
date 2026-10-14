#!/usr/bin/env bash
# одноразовая регистрация точечного NOPASSWD для wg-manage.sh (выполняется root'ом)
set -euo pipefail
cat > /etc/sudoers.d/wg-manage <<'EOF'
molochko ALL=(root) NOPASSWD: /usr/bin/bash /home/molochko/agent-tools/wg-manage.sh *
EOF
chmod 440 /etc/sudoers.d/wg-manage
visudo -cf /etc/sudoers.d/wg-manage
chown root:root /home/molochko/agent-tools/wg-manage.sh /home/molochko/agent-tools/wg1-add-friend.sh
chmod 755 /home/molochko/agent-tools/wg-manage.sh /home/molochko/agent-tools/wg1-add-friend.sh
ls -la /home/molochko/agent-tools/wg-manage.sh /home/molochko/agent-tools/wg1-add-friend.sh /etc/sudoers.d/wg-manage
echo SUDOERS-READY
