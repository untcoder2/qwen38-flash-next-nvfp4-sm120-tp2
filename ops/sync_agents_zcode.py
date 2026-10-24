# Sync global operator rules: Kimi (canonical) -> ZCode user-scope AGENTS.md.
# Source of truth stays byte-identical: C:/Users/abcd/.kimi-code/AGENTS.md.
# The generated copy applies mechanical rewrites (Kimi-isms that ZCode's
# harness doesn't have). Every rewrite MUST match the source — zero matches
# means the canonical drifted and this file needs updating (hard fail, like
# sync_skills_zcode.py's frontmatter validator). Kimi's CLAUDE.md is NOT
# synced: it is a workaround for a Kimi ReadMediaFile bug, irrelevant to ZCode.
# Re-run after editing the canonical: py -3 D:/llm/ops/sync_agents_zcode.py
import re
import sys
from pathlib import Path

SRC = Path('C:/Users/abcd/.kimi-code/AGENTS.md')
DST = Path('C:/Users/abcd/.zcode/AGENTS.md')

BANNER = (
    '<!-- СГЕНЕРИРОВАНО ops/sync_agents_zcode.py из ~/.kimi-code/AGENTS.md — '
    'не править руками; канон правим там и ресинхронимся. -->\n'
)

# (pattern, replacement, must-appear-at-least-once)
REWRITES = [
    (r'whenToUse', 'when_to_use', True),
    (r'Ро́и\s*\(AgentSwarm\) и любые', 'Любые', True),
    (r'`Agent`/`AgentSwarm`', '`Agent`', True),
    (r'рёев', 'роев', True),
    (r'xhigh-effort\s*пул-записи', 'xhigh-effort модели', True),
    (r'\(`model:.*?использовать\)',
     'в Kimi это был `local/qwen3.8-flash-next-xhigh`; в ZCode — соответствующий '
     'xhigh-профиль из конфига провайдера, если объявлен; у дефолтного профиля '
     'effort medium — для ревью его НЕ использовать', True),
]

FORBIDDEN_IN_OUTPUT = ['AgentSwarm', 'config.toml', 'KIMI_SKILL_DIR', 'whenToUse']


def convert(text: str) -> str:
    missing = []
    for pat, rep, required in REWRITES:
        text, n = re.subn(pat, rep, text, flags=re.DOTALL)
        if required and n == 0:
            missing.append(pat)
    if missing:
        raise SystemExit('РАСХОЖДЕНИЕ: канон ~/.kimi-code/AGENTS.md больше не содержит '
                         'паттернов, адаптированных здесь:\n  ' + '\n  '.join(missing) +
                         '\nПравь REWRITES в этом скрипте, не выходной файл.')
    head, sep, rest = text.partition('\n')
    if sep:
        text = head + '\n\n' + BANNER + sep.lstrip('\n') + rest
    return text


def main() -> int:
    check = '--check' in sys.argv[1:]
    src = SRC.read_text(encoding='utf-8')
    out = convert(src)
    for bad in FORBIDDEN_IN_OUTPUT:
        if bad in out:
            raise SystemExit(f'Валидация: в выходе остался Kimi-термин {bad!r} — '
                             f'добавь переписывание в REWRITES.')
    if len(out.splitlines()) >= 200:
        raise SystemExit('Валидация: файл правил >=200 строк, проверь что не нарастил мусора.')
    old = DST.read_text(encoding='utf-8') if DST.exists() else None
    if old == out:
        print(f'{"--check":8}: 0 изменений ({DST})' if check else f'OK: 0 изменений (байт в байт)')
        return 0
    if check:
        print(f'--check: РАСХОЖДЕНИЕ — {DST} отстал от канона, запусти без --check')
        return 1
    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(out, encoding='utf-8', newline='\n')
    print(f'OK: {DST} перегенерирован из канона ({len(out)} байт)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
