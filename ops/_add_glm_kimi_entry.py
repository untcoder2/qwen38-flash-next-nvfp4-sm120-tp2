#!/usr/bin/env python3
"""Добавляет подключение local/glm-5.3-flash-shadow в ~/.kimi-code/config.toml.
Backup-before, идемпотентно, томл-валидация после. model пока = qwen3.8-flash-next
(единственное served-name); при подтверждении двух имён в vLLM — swap на glm-5.3-flash."""
import shutil, sys, time, tomllib, pathlib

CFG = pathlib.Path.home() / ".kimi-code" / "config.toml"
KEY = '[models."local/glm-5.3-flash-shadow"]'
ENTRY = '''
[models."local/glm-5.3-flash-shadow"]
provider = "local"
model = "qwen3.8-flash-next"
max_context_size = 1048576
capabilities = [ "tool_use", "thinking", "image_in" ]
display_name = "GLM-5.3-Flash (shadow)"
adaptive_thinking = true

[models."local/glm-5.3-flash-shadow".overrides]
support_efforts = [ "low", "high", "max" ]
default_effort = "high"
'''

raw = CFG.read_text(encoding="utf-8")
if KEY in raw:
    print("already present — no-op")
    sys.exit(0)
bak = CFG.with_name(f"config.toml.bak-glm-{time.strftime('%Y%m%d-%H%M%S')}")
shutil.copy2(CFG, bak)

# вставка перед секцией [[hooks]], чтобы модели остались рядом с моделями
marker = "[[hooks]]"
idx = raw.find(marker)
if idx == -1:
    sys.stderr.write("нет маркера [[hooks]] — прервано, конфиг не тронут\n")
    sys.exit(1)
new = raw[:idx].rstrip() + "\n" + ENTRY + "\n" + raw[idx:]
CFG.write_text(new, encoding="utf-8")

d = tomllib.loads(CFG.read_text(encoding="utf-8"))  # parse-fail = throw
ok = "local/glm-5.3-flash-shadow" in d.get("models", {})
hooks = len(d.get("hooks", []))
prov = d.get("providers", {}).get("local", {}).get("base_url")
print(f"backup: {bak.name} | entry: {ok} | hooks: {hooks} | local provider: {prov}")
print("default_model:", d.get("default_model"))
if not ok or hooks != 4:
    sys.stderr.write("верификация не пройдена — восстановить из backup\n")
    sys.exit(2)
print("OK")
