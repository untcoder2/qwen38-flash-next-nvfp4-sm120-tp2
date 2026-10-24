# PostToolUse hook (Edit/Write): синтаксис-гейт. После правки .py — py_compile,
# .sh — bash -n. Падение — exit 2 +stderr: агент видит ошибку сразу, а не через
# 10 минут «почему скрипт не стартует». Не формат и не линт — только парсер.
import json
import os
import subprocess
import sys

BASH = r'C:\Program Files\Git\bin\bash.exe'

def err(msg):
    sys.stderr.buffer.write(msg.encode('utf-8', 'replace'))

def main():
    try:
        data = json.load(sys.stdin)
        fp = (data.get('tool_input') or {}).get('file_path') or ''
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
            err('guard_post: СИНТАКСИС-ОШИБКА в свежей правке '
                f'{os.path.basename(fp)}:\n{out[:1500]}\n')
            return 2
    except Exception:
        return 0  # не ломать сессию инфраструктурной ошибкой хука
    return 0

if __name__ == '__main__':
    sys.exit(main())
