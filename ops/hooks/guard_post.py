# PostToolUse hook (Edit/Write): синтаксис-гейт. После правки .py — py_compile,
# .sh — bash -n. Канал фидбека зависит от хоста: ZCode — exit 2 + stderr;
# Kimi — observe-only, поэтому notice в stdout при exit 0. Не формат и не линт —
# только парсер.
import json
import os
import subprocess
import sys

try:  # stdout/stderr хука — строго UTF-8: Kimi читает их как UTF-8, а Windows
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

BASH = r'C:\Program Files\Git\bin\bash.exe'

def err(msg):
    sys.stderr.buffer.write(msg.encode('utf-8', 'replace'))

def main():
    try:
        data = json.load(sys.stdin)
        kimi = 'kimi' in str(data.get('client_type', '')).lower()
        ti = data.get('tool_input') or {}
        fp = ti.get('file_path') or ti.get('path') or ''   # Kimi шлёт path, ZCode — file_path
        fp = os.path.normpath(str(fp))
        if not fp.lower().endswith(('.py', '.sh')) or not os.path.isfile(fp):
            return 0
        if fp.lower().endswith('.py'):
            cmd = [sys.executable, '-m', 'py_compile', fp]
        else:
            if not os.path.isfile(BASH):
                return 0
            cmd = [BASH, '-n', fp]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=20)
        if r.returncode != 0:
            out = (r.stderr or r.stdout or '').strip()
            notice = ('guard_post: СИНТАКСИС-ОШИБКА в свежей правке '
                      f'{os.path.basename(fp)}:\n{out[:1500]}\n')
            if kimi:
                # Kimi: PostToolUse observe-only, а stdout присоединяется к
                # контексту только при exit 0 — значит фидбек агенту это print,
                # и выходить надо нулём.
                print('HOOK NOTICE: ' + notice.replace('\n', ' | ')[:800])
                return 0
            err(notice)
            return 2
    except Exception:
        return 0  # не ломать сессию инфраструктурной ошибкой хука
    return 0

if __name__ == '__main__':
    sys.exit(main())
