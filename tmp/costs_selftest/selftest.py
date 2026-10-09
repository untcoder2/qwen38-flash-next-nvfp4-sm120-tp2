#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Selftest для D:\\llm\\dashboard\\collect_costs.py (вне общей директории, не коммитится).
Покрывает: schema, формулу, 4 пути источника wire (argument/collect_wire/wire.json/zeros),
устойчивость к битому collect_wire, реальные wire-логи за 30 дней."""
import glob, json, os, sys, time, types
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
sys.path.insert(0, r"D:\llm\dashboard")
import collect_costs as cc  # noqa: E402

FAILED = []

def check(name, cond, info=""):
    status = "OK " if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {info}" if info and not cond else ""))
    if not cond:
        FAILED.append(name)

# ---------- 1. схема и zeros-путь (глушим импорт и json) ----------
cc_collect = cc.collect
orig_import, orig_json = cc._wire_from_import, cc._wire_from_json
cc._wire_from_import = lambda days: None
cc._wire_from_json = lambda: None
out = cc.collect(30)
check("zeros: wire_source", out["wire_source"] == "zeros", out["wire_source"])
check("zeros: totals нули", out["totals"] == {"prompt": 0, "gen": 0, "cache_read": 0})
check("zeros: hours=30*24", out["local"]["kwh"] == round(720 * 700 / 1000, 1), out["local"])
check("zeros: rub", out["local"]["rub"] == round(504.0 * 10), out["local"])
check("schema: ключи costs", set(["updated_at", "totals", "cloud", "local"]) <= set(out))
check("schema: totals поля", set(out["totals"]) == {"prompt", "gen", "cache_read"})
check("schema: cloud 9 рядок", len(out["cloud"]) == 9, len(out["cloud"]))
for i, r in enumerate(out["cloud"]):
    check(f"schema: cloud[{i}] поля",
          set(r) == {"vendor", "model", "in_usd", "cached_in_usd", "out_usd", "est_usd"}, str(r))
check("schema: local поля", set(out["local"]) == {"kwh", "rub", "note"})
check("cloud отсортирован по убыв est_usd",
      all(a["est_usd"] >= b["est_usd"] for a, b in zip(out["cloud"], out["cloud"][1:])))
check("note про приблизительность", "сверя" in out["note"].lower() or "приблиз" in out["note"].lower(), out["note"])
check("local note про RTX PRO 6000", "RTX PRO 6000" in out["local"]["note"], out["local"]["note"])
check("updated_at ISO с таймзоной", "+" in out["updated_at"] or "T" in out["updated_at"], out["updated_at"])

# ---------- 2. formula против ручного расчёта (argument-путь) ----------
P, G, CR = 10_000_000, 2_000_000, 6_000_000  # prompt, gen, cache_read
arg = {"totals": {"prompt": P, "gen": G, "cache_read": CR},
       "days": [{"date": f"2026-10-0{d}", "steps": d, "prompt": 1, "gen": 1} for d in range(1, 9)]}
out = cc.collect(30, wire_totals=arg)
check("arg: wire_source", out["wire_source"] == "argument")
check("arg: totals проброшены", out["totals"] == {"prompt": P, "gen": G, "cache_read": CR})
ds = out["cloud"][0]  # дорогой рядок для сверки
p = next(x for x in cc.PRICES if x["vendor"] == ds["vendor"] and x["model"] == ds["model"])
manual = CR / 1e6 * p["cached_in_usd"] + (P - CR) / 1e6 * p["in_usd"] + G / 1e6 * p["out_usd"]
check("arg: формула est_usd", abs(ds["est_usd"] - round(manual, 2)) < 1e-9, f"{ds['est_usd']} vs {manual}")
check("arg: часы из активных wire-дней", out["local"]["kwh"] == round(8 * 24 * 700 / 1000, 1), out["local"])
# cache_read > prompt клампится
out2 = cc.collect(30, wire_totals={"prompt": 100, "gen": 0, "cache_read": 999})
check("кламп cache_read<=prompt", out2["totals"]["cache_read"] == 100, out2["totals"])
# голые тоталы без обёртки totals тоже принимаются, мусор не роняет
out3 = cc.collect(30, wire_totals={"prompt": "abc", "gen": None, "cache_read": 5})
check("мусор в тоталах не роняет", out3["totals"] == {"prompt": 0, "gen": 0, "cache_read": 0}, out3["totals"])

# ---------- 3. fallback на data/wire.json (monkeypatch DATA_DIR) ----------
cc._wire_from_json = orig_json  # настоящий читатель json; заглушка импорта collect_wire остаётся
tmpdir = Path(r"D:\llm\tmp\costs_selftest\fake_data")
tmpdir.mkdir(parents=True, exist_ok=True)
(tmpdir / "wire.json").write_text(json.dumps({
    "updated_at": "x", "totals": {"steps": 10, "prompt": 5_000_000, "gen": 500_000, "cache_read": 3_000_000},
    "days": [{"date": "2026-10-01", "steps": 4, "prompt": 1, "gen": 1},
             {"date": "2026-10-02", "steps": 0, "prompt": 0, "gen": 0}]}, ensure_ascii=False), encoding="utf-8")
orig_dir = cc.DATA_DIR
cc.DATA_DIR = tmpdir
out = cc.collect(30)
check("wire.json fallback: source", out["wire_source"] == "data/wire.json", out["wire_source"])
check("wire.json fallback: totals", out["totals"]["prompt"] == 5_000_000)
check("wire.json fallback: 1 активный день → kwh", out["local"]["kwh"] == round(24 * 700 / 1000, 1), out["local"])
(tmpdir / "wire.json").write_text("{broken json", encoding="utf-8")
out = cc.collect(30)
check("битый wire.json → zeros, не падает", out["wire_source"] == "zeros")
cc.DATA_DIR = orig_dir
cc._wire_from_import, cc._wire_from_json = orig_import, orig_json  # вернуть боевые реализации

# ---------- 4. импорт collect_wire работает и битый не роняет ----------
fake = types.ModuleType("collect_wire")
fake.collect = lambda days: {"totals": {"prompt": 111, "gen": 22, "cache_read": 5}, "days": []}
sys.modules["collect_wire"] = fake
out = cc.collect(30)
check("collect_wire import: source", out["wire_source"] == "collect_wire", out["wire_source"])
check("collect_wire import: totals", out["totals"]["prompt"] == 111)
check("collect_wire days=[] → 24/7", out["local"]["kwh"] == round(720 * 700 / 1000, 1), out["local"])

def boom(days):
    raise RuntimeError("сломанный коллектор")
fake.collect = boom
out = cc.collect(30)
check("битый collect_wire → tier 2 (data/wire.json или zeros)", out["wire_source"] in ("data/wire.json", "zeros"), out["wire_source"])
cc._wire_from_json = lambda: None  # и оба источника сломаны → zeros
out = cc.collect(30)
check("битый collect_wire + нет wire.json → zeros, не падает", out["wire_source"] == "zeros", out["wire_source"])
cc._wire_from_json = orig_json
del sys.modules["collect_wire"]

# ---------- 5. summary-строка ----------
s = cc.summary_line(cc.collect(30, wire_totals={"prompt": P, "gen": G, "cache_read": CR}))
check("summary одна строка", "\n" not in s, repr(s))
check("summary: min/max/₽ на месте", s.count("$") == 2 and "₽" in s and "кВт" in s, s)
check("summary: разделители тысяч", "10 000 000" in s, s)
print("     summary →", s)

# ---------- 6. реальные wire-логи за 30 дней (референсный парсер из CONTRACT) ----------
ROOT = os.path.expanduser("~/.kimi-code/sessions")
cutoff_ms = time.time() * 1000 - 30 * 3600 * 1000
tot = {"prompt": 0, "gen": 0, "cache_read": 0}
per_day = {}
nfiles = 0
for wf in glob.glob(os.path.join(ROOT, "wd_*", "session_*", "agents", "*", "wire.jsonl")):
    nfiles += 1
    with open(wf, encoding="utf-8", errors="ignore") as f:
        for line in f:
            if '"usage"' not in line or '"step.end"' not in line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("time", 0) < cutoff_ms:
                continue
            u = (r.get("event") or {}).get("usage") or {}
            if not u:
                continue
            pr = u.get("inputOther", 0) + u.get("inputCacheRead", 0) + u.get("inputCacheCreation", 0)
            g = u.get("output", 0)
            d = time.strftime("%Y-%m-%d", time.localtime(r["time"] / 1000))
            dd = per_day.setdefault(d, {"date": d, "steps": 0, "prompt": 0, "gen": 0})
            dd["steps"] += 1; dd["prompt"] += pr; dd["gen"] += g
            tot["prompt"] += pr; tot["gen"] += g; tot["cache_read"] += u.get("inputCacheRead", 0)
print(f"     реальных wire-файлов: {nfiles}, prompt={tot['prompt']:,}, gen={tot['gen']:,}, cache_read={tot['cache_read']:,}")
real = cc.collect(30, wire_totals={"totals": tot, "days": sorted(per_day.values(), key=lambda x: x["date"])})
check("реальные данные: парсится без ошибок", real["wire_source"] == "argument")
check("реальные данные: est_usd монотонны по прайсу",
      real["cloud"][0]["est_usd"] >= real["cloud"][-1]["est_usd"] >= 0)
if tot["prompt"] + tot["gen"] > 0:
    check("реальные данные: ненулевые оценки", real["cloud"][0]["est_usd"] > 0)
    check("реальные данные: часы из активных дней",
          real["local"]["kwh"] == round(len([d for d in per_day if per_day[d]["steps"] > 0]) * 24 * 700 / 1000, 1))
print("     summary (реальные) →", cc.summary_line(real))

# ---------- 7. CLI-прогон: data/costs.json валиден ----------
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
out = cc.main([])
jp = Path(r"D:\llm\dashboard\data\costs.json")
check("CLI: data/costs.json создан", jp.exists())
loaded = json.loads(jp.read_text(encoding="utf-8"))  # упадёт на битом json — это и есть проверка
check("CLI: json совпадает с collect()", loaded["totals"] == out["totals"] and len(loaded["cloud"]) == 9)
check("CLI: ensure_ascii=False — кириллица живая", "кВт" in jp.read_text(encoding="utf-8") or "₽" in jp.read_text(encoding="utf-8"))

print("\n" + ("ВСЕ ТЕСТЫ ПРОЙДЕНЫ" if not FAILED else f"ПРОВАЛЕНО: {FAILED}"))
sys.exit(1 if FAILED else 0)
