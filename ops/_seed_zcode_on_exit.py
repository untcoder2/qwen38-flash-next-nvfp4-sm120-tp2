# Detached one-shot: wait until ZCode.exe exits, let its shutdown flush of
# setting.json land (~3 s), then run seed_zcode_projects.py so the migrated
# project list survives. Spawned 24.10.2026 hidden via Start-Process.
import subprocess
import sys
import time

def running() -> bool:
    r = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq ZCode.exe'],
                       capture_output=True, text=True)
    return 'ZCode.exe' in r.stdout

deadline = time.time() + 8 * 3600
while running():
    if time.time() > deadline:
        sys.exit(0)
    time.sleep(1)
time.sleep(3)
subprocess.run([sys.executable, 'D:/llm/ops/seed_zcode_projects.py'])
