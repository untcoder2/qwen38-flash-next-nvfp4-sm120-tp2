# PreToolUse hook (Bash): жёсткие блокировки «необратимого» из AGENTS.md,
# которые раньше держались только на честном слове скилов. Exit 2 = deny.
# Обход: команда должна содержать маркер `owner-go` — его ставит агент ТОЛЬКО
# после явного «да» владельца именно на этот шаг (жёсткий стоп 27.09/19.09).
# Schema-safe: не разобрали stdin — молча пропускаем (fail-open, не ломаем сессию).
import json
import re
import sys

def err(msg):  # строго UTF-8 в stderr: консольная кодовая страница Windows иначе даст кракозябры
    sys.stderr.buffer.write(msg.encode('utf-8', 'replace'))

DENY = [
    (r'docker\s+image\s+prune', 'image prune запрещён (rollback-теги, docs/rollback.md)'),
    (r'(echo[^|;\n]*>>?\s*\S*|>>?\s*\S*)active\.cfg', 'смена active.cfg — только владельцем (жёсткий стоп)'),
    (r'systemctl\s+(restart|stop)\s+(sglang|vllm-watchdog)',
     'restart/stop sglang/vllm-watchdog — жёсткий стоп: нужен маркер owner-go и явное «да» владельца'),
    (r'docker\s+rm\s+(-f\s+|--force\s+)?"?penny\b',
     'снос контейнера penny = остановка инфера вместе с живыми сессиями; только владелец'),
    (r'ops/rsh\.sh.*\bsudo\b', 'sudo на хосте — вне NOPASSWD-списка это пароль владельца; '
     'читательные sudo -n -l / status / is-active пропускаются'),
    # промпт-корни (агент-файлы = system prompt, гарды, правила): shell-запись
    # только с owner-go после явного «да» владельца (25.10, волна 3 HIGH-1/L5).
    (r'>+\s*"?[^\n|;]*(\.agents[/\\]|ops[/\\]hooks[/\\]|AGENTS\.md)',
     'перенаправление в промпт-корень (.agents/, ops/hooks/, AGENTS.md) — остановочное «да»'),
    (r'\btee\b[^\n|;]*(\.agents[/\\]|ops[/\\]hooks[/\\]|AGENTS\.md)',
     'tee в промпт-корень — остановочное «да»'),
    (r'\bsed\s+-[a-zA-Z]*i[^\n|;]*(\.agents[/\\]|ops[/\\]hooks[/\\]|AGENTS\.md)',
     'sed -i по промпт-корню — остановочное «да»'),
    (r'\bgit\s+add\s+(-A|--all|\.)\s|\bgit\s+add\s+\.$',
     'git add -A/. запрещён: в дереве чужой WIP, стейдж точечным списком'),
]
SUDO_OK = re.compile(r'sudo\s+-n\s+(-l|--list)\b|systemctl\s+status|is-active|journalctl', re.I)
# Мутационные глаголы: команда с ними НЕ считается читательской, даже если
# read-only маркер в ней тоже есть (24.10: подстроковый SUDO_OK обходился
# составной «…status && …restart» внутри кавычек rsh.sh — кавычки резать
# нельзя, поэтому fail-closed по всей строке).
SUDO_MUT = re.compile(r'\b(restart|stop|start|apply|arm|disarm|reboot|poweroff|halt|flush|'
                      r'rm\b|rmdir|mv\b|dd\b|truncate|tee\b|sed\s+-i)|>\s*/etc', re.I)


def _sudo_readonly(cmd: str) -> bool:
    return bool(SUDO_OK.search(cmd)) and not SUDO_MUT.search(cmd)


def main():
    try:
        data = json.load(sys.stdin)
        cmd = str(data.get('tool_input', {}).get('command', ''))
    except Exception:
        return 0
    for pat, why in DENY:
        if re.search(pat, cmd, re.I) and 'owner-go' not in cmd:
            if 'sudo' in pat and _sudo_readonly(cmd):
                continue
            err(f'guard_bash: БЛОК — {why}. Если владелец только что сказал «да» на '
                f'этот конкретный шаг — повтори команду с комментарием `owner-go` '
                f'в конце строки. Без такого «да» не обходить.\n')
            return 2
    return 0

if __name__ == '__main__':
    sys.exit(main())
