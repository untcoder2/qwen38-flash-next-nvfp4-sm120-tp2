#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Golden-снапшот GPU-стенда penny. Читает всё, пишет только в --root (+ docker tag при --pin)."""
import argparse, glob, hashlib, json, os, re, subprocess, sys, time, urllib.request

MODEL_DIR = "/home/OPERATOR/vllm-models/Qwen3.8-Flash-Next-NVFP4-bf16mtp"
KEY_FILES = [
    "/home/OPERATOR/penny/tp2-serve-flash-next.sh",
    "/home/OPERATOR/penny/tp2-chat-template.sh",
    "/home/OPERATOR/penny/tp2-ple-backend.sh",
    "/home/OPERATOR/penny/serve-flash-next.sh",
    "/home/OPERATOR/penny/serve-flash-next-frspec.sh",
    "/home/OPERATOR/vllm/serve-penny.sh",
    "/home/OPERATOR/vllm/serve-active.sh",
    os.path.join(MODEL_DIR, "config.json"),
    os.path.join(MODEL_DIR, "generation_config.json"),
    os.path.join(MODEL_DIR, "hf_quant_config.json"),
    os.path.join(MODEL_DIR, "model.safetensors.index.json"),
]
ENV_WANTED = ("CUDA_VISIBLE_DEVICES", "SGLANG_ALLOW_OVERWRITE_LONGER_CONTEXT_LEN",
              "SGLANG_SM120_ONLINE_MXFP8", "TARGET_MODEL", "LD_LIBRARY_PATH",
              "SGLANG_ENABLE_SPEC_V2")

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()

ap = argparse.ArgumentParser()
ap.add_argument("--label", required=True)
ap.add_argument("--container", default="penny")
ap.add_argument("--root", default="/home/OPERATOR/penny/snapshots")
ap.add_argument("--pin", action="store_true", help="docker tag снимка в rollback:keep-<label>")
ap.add_argument("--hash-weights", action="store_true", help="sha256 всех safetensors (тяжело)")
a = ap.parse_args()

out = os.path.join(a.root, time.strftime("%Y%m%d-%H%M%S") + "--" + a.label)
os.makedirs(os.path.join(out, "files"), exist_ok=True)

man = {"label": a.label, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "host": sh("hostname")}

# 1. контейнер и образ — по digest, не по тегу
insp = json.loads(sh("docker inspect %s" % a.container) or "[]")
if not insp:
    print("ERROR: контейнер %s не найден — снапшот НЕ снят" % a.container); sys.exit(2)
ins = insp[0]
img = json.loads(sh("docker image inspect %s --format '{{json .}}'" % ins["Image"]) or "{}")
man["container"] = {
    "name": a.container, "image_full": ins["Image"],
    "repo_tags_at_snapshot": img.get("RepoTags"), "repo_digests_at_snapshot": img.get("RepoDigests"),
    "state": ins["State"]["Status"], "started": ins["State"]["StartedAt"],
    "restart_policy": ins["HostConfig"]["RestartPolicy"]["Name"],
}

# 2. реальные cmdline живых процессов (источник истины вместо скриптов)
procs = []
pat = re.compile(r"sglang serve|docker run .*--name %s\b|serve-flash-next|sglang\.launch_server" % re.escape(a.container))
for cl_path in glob.glob("/proc/[0-9]*/cmdline"):
    try:
        pid = int(cl_path.split("/")[2])
        cl = open(cl_path, "rb").read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
    except Exception:
        continue
    if not cl or not pat.search(cl):
        continue
    env = {}
    try:
        raw = open(cl_path.replace("cmdline", "environ"), "rb").read().split(b"\0")
        for kv in raw:
            s = kv.decode("utf-8", "replace")
            if "=" in s and s.split("=", 1)[0] in ENV_WANTED:
                env[s.split("=", 1)[0]] = s.split("=", 1)[1]
    except Exception:
        pass
    procs.append({"pid": pid, "cmdline": cl, "env": env})
man["processes"] = procs

# 3. рантайм-истина из сервера
srv_keys = ("model_path", "sampling_defaults", "preferred_sampling_params",
            "default_chat_template_kwargs", "quantization", "speculative_algorithm",
            "speculative_num_steps", "speculative_eagle_topk", "speculative_num_draft_tokens",
            "speculative_draft_model_quantization", "context_length", "max_running_requests",
            "mem_fraction_static", "kv_cache_dtype", "tp_size", "chunked_prefill_size",
            "mamba_track_interval", "max_mamba_cache_size")
try:
    srv = json.loads(urllib.request.urlopen("http://127.0.0.1:1234/get_server_info", timeout=15).read().decode())
    man["server_info"] = {k: srv.get(k) for k in srv_keys}
except Exception as e:
    man["server_info"] = {"ERROR": repr(e)}

# 4. конфигурационные файлы: хеш + копия <2MB c проверкой
inv = []
seen = set()
paths = list(KEY_FILES) + sorted(glob.glob("/home/OPERATOR/penny/*.sh")) + sorted(glob.glob("/home/OPERATOR/penny/*.sh.bak-*"))
for path in paths:
    if path in seen:
        continue
    seen.add(path)
    if not os.path.exists(path):
        inv.append({"path": path, "missing": True}); continue
    st = os.stat(path)
    ent = {"path": path, "size": st.st_size,
           "mtime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime)),
           "sha256": sha256(path)}
    if os.path.islink(path):
        ent["symlink_to"] = os.path.realpath(path)
    if st.st_size < 2 * 1024 * 1024:
        dst = os.path.join(out, "files", path.replace("/", "_"))
        tmp = dst + ".tmp"
        with open(path, "rb") as f, open(tmp, "wb") as g:
            g.write(f.read()); g.flush(); os.fsync(g.fileno())
        if sha256(tmp) != ent["sha256"]:
            print("ERROR: копия не совпала по хешу:", path); sys.exit(3)
        os.replace(tmp, dst)
        ent["copy"] = os.path.relpath(dst, out)
    inv.append(ent)
man["files"] = inv

# 5. инвентарь модели (размеры; хеши тяжёлых шардов — по флагу)
wls = []
for root, _, files in os.walk(MODEL_DIR):
    for fn in sorted(files):
        fp = os.path.join(root, fn)
        st = os.stat(fp)
        e = {"path": fp, "size": st.st_size}
        if fn.endswith(".safetensors"):
            e["sha256"] = sha256(fp) if a.hash_weights else None
        wls.append(e)
man["model_dir"] = {"path": MODEL_DIR, "n_files": len(wls), "files": wls}

# 6. манифест: атомарно + обратное чтение
data = json.dumps(man, ensure_ascii=False, indent=1)
tmp = os.path.join(out, "MANIFEST.json.tmp")
with open(tmp, "w", encoding="utf-8") as f:
    f.write(data); f.flush(); os.fsync(f.fileno())
os.replace(tmp, os.path.join(out, "MANIFEST.json"))
json.load(open(os.path.join(out, "MANIFEST.json"), encoding="utf-8"))  # читается?

if a.pin:
    tag = "rollback:keep-%s" % a.label
    r = subprocess.run("docker tag %s %s" % (man["container"]["image_full"], tag),
                       shell=True, capture_output=True, text=True)
    man["pinned_tag"] = tag if r.returncode == 0 else "FAILED: " + r.stderr
    with open(os.path.join(out, ".pinok"), "w", encoding="utf-8") as f:
        f.write(man["pinned_tag"])
    # перезаписать манифест с пином
    tmp = os.path.join(out, "MANIFEST.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(man, ensure_ascii=False, indent=1)); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, os.path.join(out, "MANIFEST.json"))

print("SNAPSHOT:", out)
print("image:", (man["container"]["image_full"] or "")[:19], "| pinned:", man.get("pinned_tag", "-"))
print("files:", sum(1 for e in inv if "sha256" in e), "| проcs:", len(procs), "| модельных файлов:", len(wls))
