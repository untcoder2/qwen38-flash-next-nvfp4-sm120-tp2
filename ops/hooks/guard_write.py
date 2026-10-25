# PreToolUse hook (Edit/Write/ApplyPatch): две функции.
# 1) DENY (exit 2): защищённые корни — ~/.kimi-code/** и kimi-sync (замороженный
#    архив, решение 24.10), агент-файлы .agents/agents/** и реализация гардов
#    ops/hooks/** (с 25.10 волна 3: агент-файлы = system prompt, их молча
#    перетирать нельзя).
# 2) ASK (JSON permissionDecision=ask): правки файлов, которые сбрасывают
#    префиксный кеш живых сессий (AGENTS.md, ~/.zcode/skills|commands|agents,
#    settings.json) — пусть человек подтвердит, как того правило о кеше.
# Не распознали вход — молча пропускаем.
import json
import os
import re
import sys

def err(msg):
    sys.stderr.buffer.write(msg.encode('utf-8', 'replace'))

ARCHIVE = re.compile(
    r'[\\/]\.kimi[\\-]code[\\/]|kimi[\\-]code[\\/]|kimi-sync[\\/]'
    r'|[\\/]\.agents[\\/]agents[\\/]|ops[\\/]hooks[\\/]', re.I)
PREFIX = re.compile(
    r'(AGENTS\.md$|CLAUDE\.md$|settings\.json$|[\\/]\.zcode[\\/](skills|commands|agents)[\\/])', re.I)

def main():
    try:
        data = json.load(sys.stdin)
        ti = data.get('tool_input', {})
        fp = ti.get('file_path') or ti.get('path') or ''
        if not fp:
            return 0
        fp = os.path.normpath(str(fp))
        kimi = 'kimi' in str(data.get('client_type', '')).lower()
    except Exception:
        return 0
    if ARCHIVE.search(fp):
        err('guard_write: БЛОК — защищённые корни (~/.kimi-code, kimi-sync, '
            '.agents/agents, ops/hooks): это промпты и гарды, правка только по '
            'явному «да» владельца; штатная доставка — python/shell с маркером '
            'owner-go в команде.\n')
        return 2
    if PREFIX.search(fp):
        reason = ('правка файла префикса (AGENTS/skills/settings) = '
                  'ре-префилл всех живых сессий. батчить правки в один заход; продолжить?')
        if kimi:
            print(json.dumps({'hookSpecificOutput': {
                'hookEventName': 'PreToolUse',
                'permissionDecision': 'ask',
                'permissionDecisionReason': reason}}))
        else:
            print(json.dumps({
                'hookEventName': 'PreToolUse',
                'permissionDecision': 'ask',
                'permissionDecisionReason': reason}))
    return 0

if __name__ == '__main__':
    sys.exit(main())
