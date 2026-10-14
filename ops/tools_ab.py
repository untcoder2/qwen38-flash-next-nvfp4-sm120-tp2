#!/usr/bin/env python3
"""tools_ab.py — при чём тут tools/thinking в запросе на /v1/messages.

Синтетика без tools дала глубокий кеш (cached=299K). Живой запрос Claude Code отличается
массивом tools (~30 MCP-тулзов) и включённым thinking. Тут: четыре варианта одного и того же
стабильного префикса, каждый двумя запросами (1 греет, 2 измеряется временем).

  plain   — system + messages
  tools   — + 12 тулзов с длинными описаниями
  think   — + thinking{adaptive}
  both    — + то и другое

python3 -I ~/agent-tools/tools_ab.py [tok]
cached читать из серверного лога: docker logs --since 2m penny | grep 'cached-token: [1-9]'
"""
import json
import ssl
import sys
import time
import urllib.request

BASE = "https://127.0.0.1:1234"
CTX = ssl._create_unverified_context()
N_LINES = max(30, (int(sys.argv[1]) if len(sys.argv) > 1 else 15000) // 2)
FILLER = "\n".join(
    f"строка {i:05d} значение {i * 104729 % 999983} — префикс опыта tools/thinking"
    for i in range(N_LINES)
)
TAG = "ZZTOOLSB2026"
TOOLS = [
    {
        "name": f"mcp__toolset_{i}__do_thing",
        "description": ("Описание инструмента " + ("x" * 120) + f" номер {i}") * 6,
        "input_schema": {
            "type": "object",
            "properties": {f"arg_{j}": {"type": "string", "description": "аргумент"} for j in range(6)},
            "required": ["arg_0"],
        },
    }
    for i in range(12)
]


def call(extra, tail, label):
    body = {
        "model": "claude-opus-5-5",
        "system": [{"type": "text", "text": f"{TAG} sys"}],
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": FILLER}]},
            {"role": "assistant", "content": [{"type": "text", "text": "принято"}]},
        ]
        + tail,
        "max_tokens": 8,
    }
    body.update(extra)
    req = urllib.request.Request(
        BASE + "/v1/messages",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer ab",
                 "anthropic-version": "2023-06-01"},
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=900) as r:
            u = json.loads(r.read()).get("usage", {})
        print(f"  {label:26s} {time.time() - t0:6.2f} с  in={u.get('input_tokens')}")
    except Exception as e:
        print(f"  {label:26s} ошибка {str(e)[:100]}")
    time.sleep(0.6)


EXTRA = {
    "plain": {},
    "tools": {"tools": TOOLS},
    "think": {"thinking": {"type": "adaptive"}},
    "both": {"tools": TOOLS, "thinking": {"type": "adaptive"}},
}

if __name__ == "__main__":
    print(f"префикс ~{len(FILLER):,} символов, tools {sum(len(json.dumps(t)) for t in TOOLS):,} байт\n")
    for name in ("plain", "tools", "think", "both"):
        print(name)
        for n in (1, 2):
            call(EXTRA[name], [{"role": "user", "content": [{"type": "text", "text": f"вопрос {n}"}]}], f"  #{n}")
