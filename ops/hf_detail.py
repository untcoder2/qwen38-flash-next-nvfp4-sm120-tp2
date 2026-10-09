#!/usr/bin/env python3
import json, urllib.request, sys
def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "ops-probe/1.0"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace"))

repo = sys.argv[1]
files = get(f"https://huggingface.co/api/models/{repo}/tree/main?recursive=true")
st = sorted([f for f in files if f["path"].endswith(".safetensors")], key=lambda f: -f["size"])
total = sum(f["size"] for f in st)
print(f"== {repo} ==")
print(f"safetensors shards: {len(st)}  total: {total/1e9:.2f} GB = {total/2**30:.2f} GiB")
print("largest 3:", [(f['path'], round(f['size']/1e9,2)) for f in st[:3]])
print("smallest:", [(f['path'], round(f['size']/1e9,2)) for f in st[-2:]])
other = {f["path"]: f["size"] for f in files if not f["path"].endswith(".safetensors")}
big = {p: s for p, s in other.items() if s > 5e8}
print("other files >0.5GB:", {p: round(s/1e9, 2) for p, s in big.items()} or "нет (двойных форматов нет)")
try:
    info = get(f"https://huggingface.co/api/models/{repo}?blobs=true")
    ss = info.get("safetensors") or {}
    print("HF safetensors meta:", ss.get("total"), "->",
          f"{(ss.get('total') or 0)/2**30:.2f} GiB" if ss.get("total") else "?")
except Exception as e:
    print("meta err", repr(e)[:100])
qnt = get(f"https://huggingface.co/api/models/{repo}/raw/main/config.json") if False else None
