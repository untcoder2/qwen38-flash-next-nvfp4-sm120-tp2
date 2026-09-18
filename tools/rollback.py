#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Атомарный откат к снапшоту. Без --yes — только dry-run диффом, ничего не меняет."""
import argparse, hashlib, json, os, shutil, subprocess, sys, time, urllib.request

OPS = "/home/OPERATOR/penny/ops"
ap = argparse.ArgumentParser()
ap.add_argument("manifest")
ap.add_argument("--yes", action="store_true")
ap.add_argument("--container", default=None)
ap.add_argument("--restart-cmd",
                default="systemctl restart sglang.service")
a = ap.parse_args()

man = json.load(open(a.manifest, encoding="utf-8"))
snap_dir = os.path.dirname(os.path.abspath(a.manifest))
container = a.container or man["container"]["name"]

def sha256(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()

# 1. Дифф: что сейчас на диске против снапшота
print("=== diff текущего состояния против снапшота ===")
to_restore = []
for ent in man["files"]:
    p = ent["path"]
    if ent.get("missing"):
        print("SKIP(missing-in-snapshot):", p); continue
    cur = sha256(p) if os.path.exists(p) else None
    if cur is None:
        print("MISSING-NOW :", p); to_restore.append(ent)
    elif cur != ent["sha256"]:
        print("DIFF        :", p, ent["sha256"][:10], "-> was", cur[:10]); to_restore.append(ent)
    else:
        print("same        :", p)

# 2. Жив ли образ снапшота (по digest!)
img = man["container"]["image_full"]
r = subprocess.run("docker image inspect %s --format ok" % img, shell=True, capture_output=True, text=True)
img_ok = r.stdout.strip() == "ok"
print("\nimage из снапшота:", img[:19], "присутствует:", img_ok)
if not img_ok:
    print("REPO-DIGESTS для ручного pull:", man["container"].get("repo_digests_at_snapshot"))
    print("ОБРАЗ ПОТЕРЯН — откат невозможен без re-pull по digest. Ничего не тронуто.")
    sys.exit(4)

if not a.yes:
    print("\nDRY-RUN завершён. К исполнению: %s --yes" % sys.argv[0]); sys.exit(0)

# 3. pre-rollback копия текущего состояния (свой снапшот, не .bak!)
pre = os.path.join(os.path.dirname(snap_dir), time.strftime("%Y%m%d-%H%M%S") + "--pre-rollback-" + man["label"])
os.makedirs(os.path.join(pre, "files"), exist_ok=True)
for ent in man["files"]:
    p = ent["path"]
    if os.path.exists(p) and not os.path.islink(p) and os.path.getsize(p) < 2 * 1024 * 1024:
        shutil.copy2(p, os.path.join(pre, "files", p.replace("/", "_")))
json.dump({"restore_point": snap_dir, "files": [e["path"] for e in man["files"]]},
          open(os.path.join(pre, "MANIFEST_PRE.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("pre-rollback сохранён:", pre)

# 4. ОСТАНОВКИ НЕ БУДЕТ: под systemd (Restart=always) docker stop = гонка на рестарт.
# Файлы восстанавливаются при работающем сервере, один systemctl restart применит всё.

# 5. Восстановление файлов: tmp+fsync+rename, хеш ПОСЛЕ записи
for ent in man["files"]:
    if ent["path"] not in [e["path"] for e in to_restore] and ent.get("copy") is None:
        continue
    dst = ent["path"]
    if ent.get("symlink_to"):
        if os.path.islink(dst) or os.path.exists(dst):
            os.unlink(dst)
        os.symlink(ent["symlink_to"], dst)
        if sha256(dst) != ent["sha256"]:
            print("ABORT: symlink content hash mismatch", dst); sys.exit(5)
        continue
    if not ent.get("copy"):
        continue
    src = os.path.join(snap_dir, ent["copy"])
    tmp = dst + ".rollback-tmp"
    with open(src, "rb") as f, open(tmp, "wb") as g:
        g.write(f.read()); g.flush(); os.fsync(g.fileno())
    if sha256(tmp) != ent["sha256"]:
        print("ABORT: restored file hash mismatch", dst); sys.exit(5)
    os.replace(tmp, dst)
print("файлы восстановлены и сверены")

# 6. Старт ТОЛЬКО через точку переключения супервизора
subprocess.run(a.restart_cmd, shell=True)
print("запуск подан:", a.restart_cmd)

# 7. Ждём здоровье (до 20 мин — NVFP4 + MTP грузятся не мгновенно)
ok = False
for i in range(120):
    time.sleep(10)
    try:
        urllib.request.urlopen("http://127.0.0.1:1234/health_generate", timeout=10)
        ok = True; break
    except Exception:
        if i % 6 == 0:
            print("... загрузка, %dс" % (i * 10))
print("health_generate:", "OK" if ok else "ТАЙМ-АУТ 20мин")

# 8. Вердикт — тем же verify.py, без права на «поднялось и ладно»
r = subprocess.run("python3 %s/verify.py %s" % (OPS, a.manifest), shell=True)
if r.returncode == 0:
    print("ROLLBACK GOOD — стенд соответствует снапшоту", man["label"])
else:
    print("\n!!! ОТКАТ НЕ ПРОШЁЛ ВАЛИДАЦИЮ. Состояние ДО отката — в", pre)
    print("!!! Откатывать откат вручную с сверкой хешей, не повторять вслепую.")
sys.exit(r.returncode)
