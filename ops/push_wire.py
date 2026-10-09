#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Пуш wire-статистики на GPU-хост: collect_wire -> scp -> ~/agent-tools/wire_push.json.
Серверный refresh.py подхватывает этот файл как секцию 'wire' (статус PUSH).
Запускать с Windows-машины, где живут wire-логи Kimi Code."""
import json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DASH = os.path.join(os.path.dirname(HERE), "dashboard")
sys.path.insert(0, DASH)
import collect_wire  # noqa: E402

HOST = "molochko@192.168.2.127"
KEY = os.path.expanduser("~/.ssh/molochko_deploy")

def main() -> int:
    days = float(sys.argv[1]) if len(sys.argv) > 1 else 30
    data = collect_wire.collect(days)
    fd, tmp = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        r = subprocess.run(["scp", "-i", KEY, "-o", "BatchMode=yes",
                            "-o", "ConnectTimeout=8", "--",
                            tmp, HOST + ":/home/molochko/agent-tools/wire_push.json"])
    finally:
        os.unlink(tmp)
    t = data["totals"]
    print(f"push_wire: шагов={t['steps']:,} prompt={t['prompt']:,} "
          f"gen={t['gen']:,} за {days:g}д -> {HOST}:agent-tools/wire_push.json "
          f"[{'ok' if r.returncode == 0 else 'SCP FAIL ' + str(r.returncode)}]")
    return r.returncode

if __name__ == "__main__":
    raise SystemExit(main())
