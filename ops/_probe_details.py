import json, urllib.request

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "ops-probe", "Accept": "application/vnd.github+json"})
    try:
        r = urllib.request.urlopen(req, timeout=20)
        return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return {"__err": e.code, "body": e.read()[:200]}
    except Exception as e:
        return {"__exc": str(e)}

REPO = "jpezzulli/sglang-rtxpro6000"

for num in (4, 8, 17, 18, 20, 9):
    it = get(f"https://api.github.com/repos/{REPO}/issues/{num}")
    if "__err" in it or "__exc" in it:
        print(f"--- #{num}: {it}"); continue
    print(f"\n===== #{num} [{it['state']}] {it['title']}")
    print("   updated:", it["updated_at"], "| comments:", it["comments"])
    body = (it.get("body") or "")
    print("   BODY:", body[:700].replace(chr(10), " ¶ "))
    cs = get(f"https://api.github.com/repos/{REPO}/issues/{num}/comments?per_page=10")
    if isinstance(cs, list):
        for c in cs[-4:]:
            print(f"   -- {c['user']['login']} {c['created_at']}: {(c.get('body') or '')[:400]}".replace(chr(10), " "))

print("\n===== PR#8 files")
for f in get(f"https://api.github.com/repos/{REPO}/pulls/8/files?per_page=10"):
    if isinstance(f, dict): print(f"   {f.get('filename')} +{f.get('additions')}/-{f.get('deletions')}")
print("===== PR#18 files")
for f in get(f"https://api.github.com/repos/{REPO}/pulls/18/files?per_page=10"):
    if isinstance(f, dict): print(f"   {f.get('filename')} +{f.get('additions')}/-{f.get('deletions')}")

# коммит 78ac080ed — тот самый docs про Swift FP8
c = get(f"https://api.github.com/repos/{REPO}/commits/78ac080ed")
if isinstance(c, dict) and "files" in c:
    print("\n===== commit 78ac080ed files:", [f.get("filename") for f in c["files"]])
