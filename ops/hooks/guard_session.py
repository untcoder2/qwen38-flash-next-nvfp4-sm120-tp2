# SessionStart hook: один раз в сессию напоминает, что среда не «голая»:
# гарды PreToolUse активны, owner-go — единственный обход, ревью-гейт обязателен.
import json
import sys

MSG = ('Хуки-гарды активны. PreToolUse БЛОКИРУЕТ: image prune (любая форма), '
       'active.cfg, restart/stop sglang|vllm-watchdog, docker rm penny, sudo вне '
       'allow-list, shell-запись (>, tee, sed -i) в .agents/, ops/hooks/, AGENTS.md, '
       'git add -A/.; обход — только маркер owner-go после явного «да» владельца '
       '(не изобретать сам; маркер тривиален текстом — перехват случайного, не '
       'граница безопасности). PreToolUse Edit/Write: DENY ~/.kimi-code-архив, '
       'kimi-sync, .agents/agents/**, ops/hooks/**; ASK на файлы префикса. '
       'PostToolUse (py_compile/bash -n) — в Kimi observe-only: notice в stdout, '
       'не блок. Ревью-гейт 18.10: пара security-reviewer+code-reviewer '
       '(model=primary) до коммита на бою, финал — arbiter. Ростер ролей: '
       '.agents/roster.md, карточки .agents/agents/.')

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
