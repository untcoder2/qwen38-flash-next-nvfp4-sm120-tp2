# PreToolUse hook (Bash): жёсткие блокировки «необратимого» из AGENTS.md,
# которые раньше держались только на честном слове скилов. Exit 2 = deny.
# Обход: команда должна содержать маркер `owner-go` — его ставит агент ТОЛЬКО
# после явного «да» владельца именно на этот шаг (жёсткий стоп 27.09/19.09).
# Schema-safe: не разобрали stdin — молча пропускаем (fail-open, не ломаем сессию).
import json
import re
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8')
    except Exception:
        pass

DENY = [
    (r'docker\s+image\s+prune', 'image prune запрещён (rollback-теги, docs/rollback.md)'),
    (r'(echo[^|;\n]*>>?\s*\S*|>>?\s*\S*)active\.cfg', 'смена active.cfg — только владельцем (жёсткий стоп)'),
    (r'systemctl\s+(restart|stop)\s+(sglang|vllm-watchdog)',
     'restart/stop sglang/vllm-watchdog — жёсткий стоп: нужен маркер owner-go и явное «да» владельца'),
]

def main():
    try:
        data = json.load(sys.stdin)
        cmd = data.get('tool_input', {}).get('command', '')
    except Exception:
        return 0
    for pat, why in DENY:
        if re.search(pat, cmd, re.I) and 'owner-go' not in cmd:
            sys.stderr.write(
                f'guard_bash: БЛОК — {why}. Если владелец только что сказал «да» на '
                f'этот конкретный шаг — повтори команду с комментарием `owner-go` '
                f'в конце строки. Без такого «да» не обходить.\n')
            return 2
    return 0

if __name__ == '__main__':
    sys.exit(main())
