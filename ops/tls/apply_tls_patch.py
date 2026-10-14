#!/usr/bin/env python3
"""Одноместный патч «включить TLS» на хосте: tp2-флаги, leaf-маунт, health watchdog.
Каждый якорь ищется ровно 1 раз; любой промах = exit 1 без записи. Бэкапы .bak-tls-1410.
После записи — bash -n синтакс-проверка всех трёх файлов."""
import subprocess, sys

def patch(path, subs, forbid=()):
    with open(path, encoding="utf-8") as f:
        t = f.read()
    for old, new in subs:
        n = t.count(old)
        if n != 1:
            sys.exit(f"ABORT {path}: якорь не уникален/не найден ({n}): {old[:60]!r}")
        t = t.replace(old, new)
    for f_ in forbid:
        if f_ in t:
            sys.exit(f"ABORT {path}: найден запрещённый маркер {f_!r}")
    with open(path + ".bak-tls-1410", "w", encoding="utf-8") as f:
        f.write(open(path, encoding="utf-8").read())
    with open(path, "w", encoding="utf-8") as f:
        f.write(t)
    subprocess.run(["bash", "-n", path], check=True)
    print("OK", path)

V = "/home/molochko/vllm"
P = "/home/molochko/penny"

# 1) tp2: ssl-флаги в launch_args (последний элемент — watchdog-timeout)
patch(f"{P}/tp2-serve-flash-next.sh", [(
    "  --watchdog-timeout 1800)",
    "  --watchdog-timeout 1800 \\\n"
    "  --ssl-keyfile /tls/server.key --ssl-certfile /tls/server.pem)",
)])

# 2) leaf: ro-маунт каталога сертификатов в /tls + строчка в шапку
patch(f"{V}/serve-penny-301.sh", [
    (
        "  -v /home/molochko/penny/cache:/cache -v /home/molochko/penny/nixl:/nixl \\",
        "  -v /home/molochko/penny/tls:/tls:ro \\\n"
        "  -v /home/molochko/penny/cache:/cache -v /home/molochko/penny/nixl:/nixl \\",
    ),
    (
        "LOCK=/home/molochko/vllm/.launch.lock",
        "# 14.10.2026: + /tls:ro — sglang TLS (--ssl-* в tp2-serve-flash-next.sh), серты ~/penny/tls.\n"
        "LOCK=/home/molochko/vllm/.launch.lock",
    ),
])

# 3) watchdog: h() на https + наш CA; forbid — если где-то дёргает 1235 по http
patch(f"{V}/watch-vllm.sh", [(
    'h() { curl -sf -m 5 "http://127.0.0.1:$1/health" >/dev/null 2>&1 || curl -sf -m 5 "http://127.0.0.1:$1/v1/models" >/dev/null 2>&1; }',
    'h() { curl -sf -m 5 --cacert /home/molochko/penny/tls/ca.pem "https://127.0.0.1:$1/health" >/dev/null 2>&1 || curl -sf -m 5 --cacert /home/molochko/penny/tls/ca.pem "https://127.0.0.1:$1/v1/models" >/dev/null 2>&1; }',
)], forbid=["h 1235"])
