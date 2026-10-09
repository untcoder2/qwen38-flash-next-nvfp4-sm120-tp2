#!/usr/bin/env python3
import json, urllib.request, sys
def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "ops-probe/1.0"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace"))

REPOS = tuple(sys.argv[1:]) or ("nvidia/GLM-5.3-NVFP4", "orcarouter/GLM-5.3-Flash-Uncensored-NVFP4")
for repo in REPOS:
    try:
        files = get(f"https://huggingface.co/api/models/{repo}/tree/main?recursive=true")
    except Exception as e:
        print(f"{repo}: ERR {e}")
        continue
    st = [f for f in files if f["path"].endswith(".safetensors")]
    total = sum(f.get("size", 0) for f in st)
    print(f"{repo}: shards={len(st)} bytes={total} GB={total/1e9:.1f} GiB={total/2**30:.1f}")
    # config for param count
    try:
        import re
        cfgp = f"https://huggingface.co/{repo}/raw/main/config.json"
        cfg = get(cfgp)
        tc = cfg.get("text_config", cfg)
        keys = ("num_hidden_layers","hidden_size","n_routed_experts","num_experts_per_tok",
                "moe_intermediate_size","max_position_embeddings","num_attention_heads")
        print("  cfg:", {k: tc.get(k) for k in keys if tc.get(k) is not None})
    except Exception as e:
        print("  cfg: ERR", repr(e)[:80])
    other = [(f["path"], f.get("size",0)) for f in files if not f["path"].endswith(".safetensors") and f.get("type")=="file" and f.get("size",0) > 5e8]
    for p, s in other:
        print(f"  big-non-safetensors: {p} {s/1e9:.2f} GB")
