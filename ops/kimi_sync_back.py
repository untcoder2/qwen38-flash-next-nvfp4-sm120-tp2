#!/usr/bin/env python3
"""Обратный синхронизатор ZCode-канон -> ~/.kimi-code (Kimi вернулся в строй).
Канон: ~/.zcode/AGENTS.md и ~/.zcode/skills. Переносим: глобальные правила
(адаптация путей), скилы (when_to_use -> whenToUse, абсолют скилл-дир).
Идемпотентно; бэкапит один раз (.bak-kimireturn). config.toml не трогаем.
"""
import re
import shutil
import sys
from pathlib import Path

ZCODE = Path.home() / ".zcode"
KIMI = Path.home() / ".kimi-code"


def bak(p: Path):
    b = p.with_suffix(p.suffix + ".bak-kimireturn")
    if p.exists() and not b.exists():
        shutil.copy2(p, b)


def sync_agents():
    src = ZCODE / "AGENTS.md"
    dst = KIMI / "AGENTS.md"
    s = src.read_text(encoding="utf-8")
    # Kimi-специфика: путь канона скилов и формулировка effort-профиля
    s = s.replace("~/.zcode/skills", "~/.kimi-code/skills")
    s = s.replace("D:\\llm\\ops\\archive\\kimi-sync", "D:\\llm\\ops")
    s = s.replace(
        "в ZCode — соответствующий xhigh-профиль из конфига провайдера, если объявлен; "
        "у дефолтного профиля effort medium — для ревью его НЕ использовать",
        "на Kimi: model="primary" (xhigh наследуется от вызывающей сессии)",
    )
    s += "\n<!-- reverse-sync от ZCode-канона: ops/kimi_sync_back.py; не править руками -->\n"
    bak(dst)
    dst.write_text(s, encoding="utf-8")
    print(f"AGENTS: {len(s)}B -> {dst}")


def sync_skills():
    n = 0
    for f in (ZCODE / "skills").glob("*/SKILL.md"):
        rel = f.relative_to(ZCODE / "skills")
        dst = KIMI / "skills" / rel
        s = f.read_text(encoding="utf-8")
        s = re.sub(r"^when_to_use:", "whenToUse:", s, flags=re.M)
        s = s.replace("${KIMI_SKILL_DIR}", str(dst.parent).replace("\\", "/"))
        dst.parent.mkdir(parents=True, exist_ok=True)
        bak(dst)
        dst.write_text(s, encoding="utf-8")
        n += 1
    print(f"skills: {n} перенесено")


if __name__ == "__main__":
    if not ZCODE.exists() or not KIMI.exists():
        sys.exit("нет канона или Kimi-дома")
    sync_agents()
    sync_skills()
    print("OK: Kimi-окружение обновлено из ZCode-канона")
