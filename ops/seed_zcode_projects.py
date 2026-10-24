# Seed ZCode Desktop sidebar (recentProjects) with the project set migrated
# from Kimi Code on 24.10.2026. The running app keeps setting.json in memory
# and rewrites it on exit, so ONLY run this with ZCode fully quit (tray too),
# then start ZCode. Idempotent merge: existing entries untouched, missing
# folders skipped, run again after re-installs without harm.
import json
import os
import sys
from pathlib import Path

P = Path('C:/Users/abcd/.zcode/v2/setting.json')
WANT = ['D:\\llm', 'D:\\vpncode', 'D:\\linuxvpn', 'D:\\router',
        'D:\\ghostlink-win', 'D:\\maga', 'D:\\qwen', 'D:\\kimi',
        'D:\\scratchpad\\secneo_lab']

def main() -> int:
    # refuse to run while the app can clobber us; lockfile probe proved too
    # weak (r+b succeeds while Electron holds it), so check processes instead
    import subprocess
    r = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq ZCode.exe'],
                       capture_output=True, text=True)
    if 'ZCode.exe' in r.stdout:
        print('STOP: ZCode запущен — закрой его полностью, включая трей, и повтори.')
        return 1
    d = json.loads(P.read_text(encoding='utf-8'))
    have = {os.path.normcase(os.path.normpath(x)) for x in d.get('recentProjects', [])}
    out = list(d.get('recentProjects', []))
    for w in WANT:
        r = os.path.normpath(w)
        if not os.path.isdir(r):
            print('skip (нет папки):', r)
            continue
        if os.path.normcase(r) not in have:
            out.append(r)
            have.add(os.path.normcase(r))
            print('add:', r)
    d['recentProjects'] = out
    P.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'готово: {len(out)} проектов. Запускай ZCode.')
    return 0

if __name__ == '__main__':
    sys.exit(main())
