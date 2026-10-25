# SessionStart hook: один раз в сессию напоминает, что среда не «голая»:
# гарды PreToolUse активны, owner-go — единственный обход, ревью-гейт обязателен.
import json
import sys

MSG = ('Хуки-гарды активны. PreToolUse БЛОКИРУЕТ: image prune, active.cfg, '
       'restart/stop sglang|vllm-watchdog, docker rm penny, sudo вне allow-list; '
       'обход — только маркер owner-go после явного «да» владельца (не изобретать '
       'сам; маркер тривиален текстом, это перехват случайного, не граница '
       'безопасности). PreToolUse Edit/Write: DENY на ~/.kimi-code-архив, ASK на '
       'файлы префикса (AGENTS/skills/settings). PostToolUse (py_compile/bash -n) '
       '— в Kimi observe-only: notice через stdout, не блокирует. Ревью-гейт: '
       'security-reviewer+code-reviewer до коммита на бою, финал — arbiter. '
       'Тяжёлое — диспатч сабагентам (roster), основная сессия координирует.')

def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
    kimi = 'kimi' in str(data.get('client_type', '')).lower()
    try:
        if kimi:
            print(json.dumps({'hookSpecificOutput': {
                'hookEventName': 'SessionStart', 'additionalContext': MSG}}))
        else:
            print(json.dumps({'hookEventName': 'SessionStart', 'additionalContext': MSG}))
    except Exception:
        pass
    return 0

if __name__ == '__main__':
    sys.exit(main())
