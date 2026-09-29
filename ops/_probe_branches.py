import json, urllib.request

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "ops-probe", "Accept": "application/vnd.github+json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return {"__err": e.code, "b": e.read()[:120]}
    except Exception as e:
        return {"__exc": str(e)[:120]}

R = "jpezzulli/sglang-rtxpro6000"
print("### per-branch recent commits (default vs each branch)")
brs = get(f"https://api.github.com/repos/{R}/branches?per_page=100")
for b in brs:
    cm = get(b["commit"]["url"])
    when = "?"
    if isinstance(cm, dict) and "commit" in cm:
        when = cm["commit"]["committer"]["date"]
    print(f"\n-- branch {b['name']}  head={b['commit']['sha'][:10]}  date={when}")
    cl = get(f"https://api.github.com/repos/{R}/commits?sha={b['name']}&per_page=6")
    if isinstance(cl, list):
        for c in cl:
            print(f"   {c['sha'][:9]} {c['commit']['author']['date']} {c['commit']['message'].splitlines()[0][:95]}")

print("\n### PR heads (unmerged work = candidate patches)")
prs = get(f"https://api.github.com/repos/{R}/pulls?state=open&per_page=20")
for p in prs:
    print(f"\n#{p['number']} [{p['state']}] {p['title'][:95]}")
    print(f"   head={p['head']['repo']['full_name']}@{p['head']['ref']} base={p['base']['ref']} mergeable_state={p.get('mergeable_state')}")
    hc = get(f"https://api.github.com/repos/{p['head']['repo']['full_name']}/commits?sha={p['head']['ref']}&per_page=3")
    if isinstance(hc, list):
        for c in hc:
            print(f"   • {c['sha'][:9]} {c['commit']['author']['date']} {c['commit']['message'].splitlines()[0][:90]}")

print("\n### our threads: new replies?")
for num in (9, 15, 17):
    cs = get(f"https://api.github.com/repos/{R}/issues/{num}/comments?per_page=20&sort=created&direction=desc")
    if isinstance(cs, list):
        print(f"#{num}: last3:", [(c['user']['login'], c['created_at']) for c in cs[:3]])

R2 = "mratsim/sglang-qwen38fn-sm120-turbo"
iss = get(f"https://api.github.com/repos/{R2}/issues?state=all&sort=updated&direction=desc&per_page=6")
print("\n### mratsim turbo, recently updated:")
if isinstance(iss, list):
    for i in iss:
        print(f"  #{i['number']} [{i['state']}] upd={i['updated_at']} {i['title'][:80]} c={i['comments']}")

print("\n### our findings repo activity (untcoder2)")
R3 = "untcoder2/qwen38-flash-next-nvfp4-sm120-tp2"
st3 = get(f"https://api.github.com/repos/{R3}/commits?per_page=3")
if isinstance(st3, list):
    for c in st3: print(f"  commit {c['sha'][:9]} {c['commit']['author']['date']} {c['commit']['message'].splitlines()[0][:70]}")
for kind in ("issues", "pulls"):
    x = get(f"https://api.github.com/repos/{R3}/{kind}?state=all&sort=updated&per_page=5")
    if isinstance(x, list):
        for i in x: print(f"  {kind} #{i['number']} [{i['state']}] upd={i['updated_at']} {i['title'][:70]}")
stars = get(f"https://api.github.com/repos/{R3}")
if isinstance(stars, dict): print(f"  stars={stars.get('stargazers_count')} forks={stars.get('forks_count')} watchers={stars.get('subscribers_count')}")
