import json, urllib.request

p = r"C:/Users/abcd/.kimi-code/sessions/wd_vpncode_8de0214b57d9/session_061aadbe-7c26-4dbb-8f8a-c6d78cc0a9f9/agents/main/wire.jsonl"
lines = open(p, encoding="utf-8").read().splitlines()
last_c = max(i for i, l in enumerate(lines) if "apply_compaction" in l)
recs = [json.loads(l) for l in lines[last_c:]]
summ = recs[0]["summary"]
msgs = [{"role": "user", "content": summ}]
cur = None

def flush():
    global cur
    if cur is not None:
        c = ("(th)" + cur["think"] + "(/th)\n" if cur["think"] else "") + cur["text"]
        m = {"role": "assistant", "content": c}
        if cur["calls"]:
            m["tool_calls"] = cur["calls"]
        if c or cur["calls"]:
            msgs.append(m)
        cur = None

for r in recs[1:]:
    t = r.get("type")
    if t == "context.append_message":
        m = r["message"]
        if m.get("role") == "user":
            flush()
            txt = "".join(c.get("text", "") for c in (m.get("content") or []))
            msgs.append({"role": "user", "content": txt})
    elif t == "context.append_loop_event":
        e = r["event"]; et = e.get("type")
        if et == "step.begin":
            flush(); cur = {"think": "", "text": "", "calls": []}
        elif et == "content.part":
            if cur is None: cur = {"think": "", "text": "", "calls": []}
            pt = e.get("part", {})
            if pt.get("type") == "think": cur["think"] += pt.get("think", "")
            elif pt.get("type") == "text": cur["text"] += pt.get("text", "")
        elif et == "tool.call":
            if cur is None: cur = {"think": "", "text": "", "calls": []}
            cur["calls"].append({"id": e["toolCallId"], "type": "function",
                                 "function": {"name": e["name"],
                                              "arguments": json.dumps(e.get("args", {}), ensure_ascii=False)}})
        elif et == "tool.result":
            flush()
            out = e.get("result", {}).get("output", "")
            if not isinstance(out, str): out = json.dumps(out, ensure_ascii=False)
            msgs.append({"role": "tool", "tool_call_id": e.get("toolCallId", ""), "content": out})
flush()

print("reconstructed messages:", len(msgs))
roles = {}
for m in msgs: roles[m["role"]] = roles.get(m["role"], 0) + 1
print(roles)
json.dump(msgs, open(r"C:/Users/abcd/AppData/Local/Temp/vpncode_ctx.json", "w", encoding="utf-8"), ensure_ascii=False)

def send(payload):
    req = urllib.request.Request("http://192.168.2.127:1234/v1/chat/completions",
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=600)
        j = json.loads(r.read())
        return 200, str(j.get("usage"))[:140]
    except urllib.error.HTTPError as ex:
        return ex.code, ex.read()[:300]
    except Exception as ex:
        return "EXC", f"{type(ex).__name__}:{str(ex)[:150]}"

print("T1 full ctx, max_tokens=64      ->", send({"model": "qwen3.8-flash-next", "messages": msgs, "max_tokens": 64, "temperature": 0}))
print("T2 full ctx, max_tokens=786432  ->", send({"model": "qwen3.8-flash-next", "messages": msgs, "max_tokens": 786432, "temperature": 0}))
