#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Перевод tp2-serve-flash-next.sh на штатный reasoning-effort.sh (v2.5.2) с
фолбэком, идентичным байт-в-байт старому хардкоду для образа 2.5.1."""
import subprocess, sys

CFG = "/home/molochko/penny/tp2-serve-flash-next.sh"
OLD = '''  --default-chat-template-kwargs '{"enable_thinking":true,"preserve_thinking":true,"reasoning_effort":"medium"}' \\\n'''
BLK_ANCHOR = 'CONTEXT_LENGTH='
BLK = '''# Pennyroyal v2.5.2: штатный хелпер tier'ов; на 2.5.1 его нет — фолбэк байт-в-байт старый JSON.
if [[ -f "$SCRIPT_DIR/reasoning-effort.sh" ]]; then
  source "$SCRIPT_DIR/reasoning-effort.sh"
else
  DEFAULT_CHAT_TEMPLATE_KWARGS='{"enable_thinking":true,"preserve_thinking":true,"reasoning_effort":"medium"}'
fi
'''

s = open(CFG, encoding="utf-8").read()
if "reasoning-effort.sh" in s:
    print("already applied"); sys.exit(0)
assert s.count(OLD) == 1, "hardcoded kwargs line not unique"
assert s.count("source \"$SCRIPT_DIR/reasoning-effort.sh\"") == 0
assert BLK_ANCHOR in s and BLK not in s
s = s.replace(OLD, '''  --default-chat-template-kwargs "$DEFAULT_CHAT_TEMPLATE_KWARGS" \\\n''')
i = s.index(BLK_ANCHOR)
s = s[:i] + BLK + s[i:]
open(CFG, "w", encoding="utf-8", newline="\n").write(s)
r = subprocess.run(["bash", "-n", CFG], capture_output=True, text=True)
if r.returncode != 0:
    print("bash -n FAILED:", r.stderr[:300]); sys.exit(1)
print("applied + syntax OK")
