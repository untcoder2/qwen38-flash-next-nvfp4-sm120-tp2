#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Коллектор wire: парсит wire-логи Kimi Code (~/.kimi-code/sessions/wd_*/session_*/agents/*/wire.jsonl)
и отдаёт секцию window.DASH.wire по CONTRACT.md.

Токены считаются по событиям step.end с usage:
  prompt = inputOther + inputCacheRead + inputCacheCreation; gen = output.
Модель берётся из ближайшей предшествующей записи llm.request того же файла
(поля model / modelAlias); если не видно — 'local/unknown'.
Скорость: побайтовое чтение + префильтр подстрокой до json.loads + пропуск
файлов с mtime старше окна."""
import glob
import json
import os
import time
from datetime import datetime

ROOT = os.path.expanduser("~/.kimi-code/sessions")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "wire.json")

_B_USAGE = b'"usage"'
_B_STEPEND = b'"step.end"'
_B_REQ = b'"llm.request"'
_B_MODEL = b'"model"'


def _new_bucket():
    return {"steps": 0, "prompt": 0, "gen": 0, "cache_read": 0, "cache_creation": 0}


def collect(days: float = 30) -> dict:
    cutoff_ms = time.time() * 1000 - days * 86400 * 1000
    totals = _new_bucket()
    per_ws = {}
    per_day = {}
    per_model = {}

    for wf in glob.glob(os.path.join(ROOT, "wd_*", "session_*", "agents", "*", "wire.jsonl")):
        try:
            if os.stat(wf).st_mtime * 1000 < cutoff_ms:
                continue  # в файле не может быть событий свежее окна
            ws = wf.split(os.sep)[-5]
        except OSError:
            continue
        acc = per_ws.setdefault(ws, _new_bucket())
        last_model = None
        with open(wf, "rb") as f:
            for line in f:
                if _B_USAGE in line and _B_STEPEND in line:
                    pass
                elif _B_REQ in line and _B_MODEL in line:
                    pass
                else:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                t = r.get("time", 0)
                if t < cutoff_ms:
                    continue
                ev = r.get("event") or {}
                if r.get("type") == "llm.request":
                    last_model = r.get("model") or r.get("modelAlias")
                    continue
                if ev.get("type") != "step.end":
                    continue
                u = ev.get("usage") or {}
                if not u:
                    continue
                p = (u.get("inputOther", 0) + u.get("inputCacheRead", 0)
                     + u.get("inputCacheCreation", 0))
                g = u.get("output", 0)
                cr = u.get("inputCacheRead", 0)
                cc = u.get("inputCacheCreation", 0)

                for bucket in (totals, acc):
                    bucket["steps"] += 1
                    bucket["prompt"] += p
                    bucket["gen"] += g
                    bucket["cache_read"] += cr
                    bucket["cache_creation"] += cc

                date = datetime.fromtimestamp(t / 1000).strftime("%Y-%m-%d")
                d = per_day.setdefault(date, {"steps": 0, "prompt": 0, "gen": 0, "cache_read": 0})
                d["steps"] += 1
                d["prompt"] += p
                d["gen"] += g
                d["cache_read"] += cr

                m = per_model.setdefault(last_model or "local/unknown",
                                         {"steps": 0, "prompt": 0, "gen": 0})
                m["steps"] += 1
                m["prompt"] += p
                m["gen"] += g

    projects = [{"ws": ws, "steps": a["steps"], "prompt": a["prompt"], "gen": a["gen"],
                 "cache_read": a["cache_read"]} for ws, a in per_ws.items()]
    projects.sort(key=lambda x: -(x["prompt"] + x["gen"]))

    day_list = [dict(d, date=date) for date, d in sorted(per_day.items())]

    models = dict(sorted(per_model.items(), key=lambda kv: -(kv[1]["prompt"] + kv[1]["gen"])))

    prompt = totals["prompt"]
    totals_out = {
        "steps": totals["steps"],
        "prompt": prompt,
        "gen": totals["gen"],
        "cache_read": totals["cache_read"],
        "cache_creation": totals["cache_creation"],
        "cache_hit_pct": round(totals["cache_read"] / prompt * 100, 1) if prompt else 0.0,
    }

    return {
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "totals": totals_out,
        "projects": projects,
        "days": day_list,
        "models": models,
    }


if __name__ == "__main__":
    import sys
    d = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
    data = collect(d)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    t = data["totals"]
    print(f"wire {d:g}d: steps={t['steps']:,} prompt={t['prompt']:,} gen={t['gen']:,} "
          f"cache_read={t['cache_read']:,} cache_hit={t['cache_hit_pct']:.1f}% "
          f"projects={len(data['projects'])} days={len(data['days'])} "
          f"models={len(data['models'])} -> {OUT}")
