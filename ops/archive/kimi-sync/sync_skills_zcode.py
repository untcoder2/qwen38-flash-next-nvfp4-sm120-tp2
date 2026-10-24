# Sync personal Kimi Code skills -> ZCode user scope (~/.zcode/skills).
# Kimi dir is the single source of truth and stays byte-identical (whenToUse,
# ${KIMI_SKILL_DIR}); this script only rewrites the copies ZCode loads, which
# drop unknown frontmatter keys and don't expand Kimi's path variable.
# Re-run after editing any skill in ~/.kimi-code/skills.
# ARCHIVED 24.10.2026: канон переехал в ZCode, не запускать (см. README).
from pathlib import Path
import re, shutil

SRC = Path('C:/Users/abcd/.kimi-code/skills')
DST = Path('C:/Users/abcd/.zcode/skills')
ZCODE_SHARED = 'C:/Users/abcd/.zcode/skills/_shared'

VALID_KEYS = {'name', 'description', 'when_to_use', 'license', 'metadata'}

def convert(text):
    # whenToUse: -> when_to_use: (Kimi's camelCase is dropped by ZCode's parser)
    text = re.sub(r'(?m)^whenToUse:', 'when_to_use:', text)
    # KIMI_SKILL_DIR/../_shared/... -> absolute ZCode path (var never expands there)
    return text.replace('${KIMI_SKILL_DIR}/../_shared', ZCODE_SHARED)

def main():
    if not SRC.is_dir():
        raise SystemExit(f'STOP: source missing: {SRC}')
    DST.mkdir(parents=True, exist_ok=True)
    changed = []
    for src_md in sorted(SRC.rglob('*.md')):
        rel = src_md.relative_to(SRC)
        dst_md = DST / rel
        new = convert(src_md.read_text(encoding='utf-8'))
        fm = re.match(r'---\n(.*?)\n---\n', new, re.S)
        if fm:
            keys = re.findall(r'(?m)^([A-Za-z_][A-Za-z0-9_-]*):', fm.group(1))
            bad = [k for k in keys if k not in VALID_KEYS]
            if bad:
                raise SystemExit(f'STOP: unrecognized frontmatter keys {bad} in {rel}')
        old = dst_md.read_text(encoding='utf-8') if dst_md.exists() else None
        if old != new:
            dst_md.parent.mkdir(parents=True, exist_ok=True)
            dst_md.write_text(new, encoding='utf-8', newline='\n')
            changed.append(str(rel))
    print(f'synced, {len(changed)} file(s) changed:')
    print('\n'.join('  ' + c for c in changed) or '  (none — already in sync)')

if __name__ == '__main__':
    main()
