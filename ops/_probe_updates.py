import json, urllib.request, datetime

def get(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": "ops-probe", "Accept": "application/json", **(headers or {})})
    try:
        r = urllib.request.urlopen(req, timeout=20)
        return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:300]
    except Exception as e:
        return "EXC", f"{type(e).__name__}: {e}"

SINCE = "2026-09-19T00:00:00Z"  # наши последние замеры/наблюдения
REPO = "jpezzulli/sglang-rtxpro6000"

print("### BRANCHES (tip dates)")
st, br = get(f"https://api.github.com/repos/{REPO}/branches?per_page=100")
if st == 200:
    for b in br:
        st2, cm = get(b["commit"]["url"].replace("api.github.com/repos/", "api.github.com/repos/"))
        # fetch commit detail lazily only for branch list date
    # simpler: use commits per branch head via ?sha=
    for b in br:
        print(f"  {b['name']:40s} head={b['commit']['sha'][:10]}")
else:
    print("  ", st, br)

print("### COMMITS since 2026-09-19 (default + all branches via pulls/commits)")
st, cm = get(f"https://api.github.com/repos/{REPO}/commits?since={SINCE}&per_page=100")
if st == 200:
    for c in cm:
        msg = c["commit"]["message"].splitlines()[0][:110]
        print(f"  {c['sha'][:9]} {c['commit']['author']['date']} {msg}")
    if not cm: print("   (default branch: пусто)")
else:
    print("  ", st, cm)

print("### RELEASES")
st, rel = get(f"https://api.github.com/repos/{REPO}/releases?per_page=10")
if st == 200:
    for r_ in rel[:6]:
        print(f"  {r_['tag_name']:20s} {r_['published_at']}  {r_['name']}")
else:
    print("  ", st, rel)

print("### PULLS (open/merged/updated since)")
st, prs = get(f"https://api.github.com/repos/{REPO}/pulls?state=all&sort=updated&direction=desc&per_page=15")
if st == 200:
    for p in prs:
        print(f"  #{p['number']} [{p['state']}{'/'+p.get('merged_at','')[:10] if p.get('merged_at') else ''}] upd={p['updated_at']} {p['title'][:90]}")
else:
    print("  ", st, prs)

print("### ISSUES updated since (incl. our #9 field reports)")
st, iss = get(f"https://api.github.com/repos/{REPO}/issues?state=all&sort=updated&direction=desc&since={SINCE}&per_page=20")
if st == 200:
    for i in iss:
        print(f"  #{i['number']} [{i['state']}] upd={i['updated_at']} {i['title'][:90]} comments={i['comments']}")
else:
    print("  ", st, iss)

print("### HF: nvidia/Qwen3.8-Flash-Next-NVFP4 discussions")
st, d = get("https://huggingface.co/api/models/nvidia/Qwen3.8-Flash-Next-NVFP4/discussions?status=open&sort=recentlyModified")
if st == 200:
    for x in d[:8]:
        print(f"  #{x.get('num')} {x.get('status')} {str(x.get('title'))[:80]}")
else:
    print("  ", st, d)

print("### HF: модель — свежие правки файлов")
st, m = get("https://huggingface.co/api/models/nvidia/Qwen3.8-Flash-Next-NVFP4")
if st == 200:
    print("  lastModified:", m.get("lastModified"), "| sha:", (m.get("sha") or "")[:12])
else:
    print("  ", st, m)

print("### HF: dicksondickson re-shard — правки после нашего 31/31")
st, u = get("https://huggingface.co/api/models/dicksondickson/Qwen3.8-Flash-Next-NVFP4-reshard-mtp-fix")
if st == 200:
    print("  lastModified:", u.get("lastModified"), "| downloads:", u.get("downloads"))
else:
    print("  ", st, u)

print("### HF: Qwen/Qwen3.8-Flash-Next (база) — новые ревизии")
st, q = get("https://huggingface.co/api/models/Qwen/Qwen3.8-Flash-Next")
if st == 200:
    print("  lastModified:", q.get("lastModified"), "| sha:", (q.get("sha") or "")[:12])
    for f in (q.get("siblings") or []):
        if f.get("rfilename","").endswith((".md",)): pass
else:
    print("  ", st, q)

print("### GitHub: mratsim/sglang-qwen38fn-sm120-turbo (r25/patch 0010)")
R2 = "mratsim/sglang-qwen38fn-sm120-turbo"
st, cm = get(f"https://api.github.com/repos/{R2}/commits?since={SINCE}&per_page=50")
if st == 200:
    for c in cm:
        print(f"  {c['sha'][:9]} {c['commit']['author']['date']} {c['commit']['message'].splitlines()[0][:100]}")
    if not cm: print("   (коммитов после 09-19 нет)")
else:
    print("  ", st, cm)
st, rel = get(f"https://api.github.com/repos/{R2}/releases?per_page=5")
if st == 200:
    for r_ in rel:
        print(f"  RELEASE {r_['tag_name']:16s} {r_['published_at']} {r_['name']}")
else:
    print("  ", st, rel)
st, iss = get(f"https://api.github.com/repos/{R2}/issues?state=all&sort=updated&direction=desc&since={SINCE}&per_page=15")
if st == 200:
    for i in iss:
        print(f"  #{i['number']} [{i['state']}] upd={i['updated_at']} {i['title'][:90]} c={i['comments']}")
else:
    print("  ", st, iss)
