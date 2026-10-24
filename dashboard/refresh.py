#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Glue-слой дашборда: гоняет collect_* in-process и пишет data.js (window.DASH).

Запуск:   python refresh.py [--days N]   (по умолчанию DEFAULT_DAYS)
Пишет:    data.js (атомарно: temp + os.replace) + зеркала секций в data/collect_*.json.
Схемы:    CONTRACT.md (раздел «СХЕМА ДАННЫХ») — единственный источник истины.

Упавший коллектор не ломает остальных: берётся предыдущий data/*.json,
а если его нет — пустая секция по схеме (UI обязан переживать пустоту).
"""
import argparse
import importlib
import json
import os
import sys
import tempfile
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
OUT_PATH = os.path.join(HERE, "data.js")
DEFAULT_DAYS = 0.0  # окно сбора; 0 = полная история (фронт режет период сам); --days N перебивает

if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:  # консоль Windows может не быть в utf-8 — не падаем на кириллице
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SECTIONS = ("wire", "server", "costs")


# ---------------------------------------------------------------- helpers ---

def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _int(v, d=0):
    try:
        if isinstance(v, bool):
            return d
        return int(float(v))
    except (TypeError, ValueError):
        return d


def _float(v, d=0.0):
    try:
        if isinstance(v, bool):
            return d
        return float(v)
    except (TypeError, ValueError):
        return d


def _str(v, d=""):
    return v if isinstance(v, str) else d


def atomic_write(path: str, text: str) -> None:
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_fallback(name: str):
    """Свежайший из data/collect_<name>.json и data/<name>.json, либо None."""
    best = None
    for fn in (f"collect_{name}.json", f"{name}.json"):
        p = os.path.join(DATA_DIR, fn)
        try:
            if not os.path.isfile(p):
                continue
            with open(p, encoding="utf-8-sig", errors="replace") as f:
                obj = json.load(f)
            if isinstance(obj, dict) and (best is None or os.path.getmtime(p) > best[0]):
                best = (os.path.getmtime(p), obj)
        except Exception:
            continue
    return best[1] if best else None


# ------------------------------------------------------------ normalizers ---
# Гарантируют, что секция соответствует схеме из CONTRACT.md, даже если
# коллектор умер/вернул мусор/отдал старый fallback-файл.

def normalize_wire(raw):
    sec = raw if isinstance(raw, dict) else {}
    t = sec.get("totals") if isinstance(sec.get("totals"), dict) else {}
    totals = {
        "steps": _int(t.get("steps")),
        "prompt": _int(t.get("prompt")),
        "gen": _int(t.get("gen")),
        "cache_read": _int(t.get("cache_read")),
        "cache_creation": _int(t.get("cache_creation")),
    }
    if t.get("cache_hit_pct") is not None:
        pct = _float(t.get("cache_hit_pct"))
    else:
        pct = (totals["cache_read"] / totals["prompt"] * 100) if totals["prompt"] else 0.0
    totals["cache_hit_pct"] = round(pct, 1)

    projects = []
    for p in (sec.get("projects") if isinstance(sec.get("projects"), list) else []):
        if not isinstance(p, dict):
            continue
        pdays = []
        for d in (p.get("days") if isinstance(p.get("days"), list) else []):
            if not isinstance(d, dict):
                continue
            date = _str(d.get("date"))
            if (len(date) != 10 or not (date[:4].isdigit() and date[4] == "-"
                and date[5:7].isdigit() and date[7] == "-" and date[8:10].isdigit())):
                continue
            pdays.append({
                "date": date,
                "steps": _int(d.get("steps")),
                "prompt": _int(d.get("prompt")),
                "gen": _int(d.get("gen")),
                "cache_read": _int(d.get("cache_read")),
            })
        pdays.sort(key=lambda x: x["date"])
        projects.append({
            "ws": _str(p.get("ws"), "?"),
            "steps": _int(p.get("steps")),
            "prompt": _int(p.get("prompt")),
            "gen": _int(p.get("gen")),
            "cache_read": _int(p.get("cache_read")),
            "days": pdays,
        })
    projects.sort(key=lambda x: -(x["prompt"] + x["gen"]))

    days = []
    for d in (sec.get("days") if isinstance(sec.get("days"), list) else []):
        if not isinstance(d, dict):
            continue
        date = _str(d.get("date"))
        if (len(date) != 10 or not (date[:4].isdigit() and date[4] == "-"
                and date[5:7].isdigit() and date[7] == "-" and date[8:10].isdigit())):
            continue
        days.append({
            "date": date,
            "steps": _int(d.get("steps")),
            "prompt": _int(d.get("prompt")),
            "gen": _int(d.get("gen")),
            "cache_read": _int(d.get("cache_read")),
        })
    days.sort(key=lambda x: x["date"])

    out = {
        "updated_at": _str(sec.get("updated_at")) or now_iso(),
        "totals": totals,
        "projects": projects,
        "days": days,
    }
    models = sec.get("models")
    if isinstance(models, dict) and models:
        out["models"] = {
            _str(k, "?"): {
                "steps": _int(v.get("steps")),
                "prompt": _int(v.get("prompt")),
                "gen": _int(v.get("gen")),
            }
            for k, v in models.items() if isinstance(v, dict)
        }
    if isinstance(sec.get("speed"), dict):
        out["speed"] = sec["speed"]  # форма проверена в collect_wire
    return out


def normalize_server(raw):
    sec = raw if isinstance(raw, dict) else {}
    c = sec.get("counters") if isinstance(sec.get("counters"), dict) else {}
    info = sec.get("info") if isinstance(sec.get("info"), dict) else {}
    # дневной ряд gen throughput движка (CONTRACT.md): [{date, n, p50, p95}],
    # полная история по возрастанию дат — период режет фронт
    gen_days = []
    for d in (sec.get("gen_tps_days") if isinstance(sec.get("gen_tps_days"), list) else []):
        if not isinstance(d, dict):
            continue
        date = _str(d.get("date"))
        if (len(date) != 10 or not (date[:4].isdigit() and date[4] == "-"
                and date[5:7].isdigit() and date[7] == "-" and date[8:10].isdigit())):
            continue
        gen_days.append({"date": date, "n": _int(d.get("n")),
                         "p50": _float(d.get("p50")), "p95": _float(d.get("p95"))})
    gen_days.sort(key=lambda x: x["date"])
    return {
        "updated_at": _str(sec.get("updated_at")) or now_iso(),
        "online": bool(sec.get("online")),
        "counters": {
            "prompt_total": _int(c.get("prompt_total")),
            "generation_total": _int(c.get("generation_total")),
        },
        "info": info,
        "gen_tps_days": gen_days,
    }


def normalize_costs(raw):
    sec = raw if isinstance(raw, dict) else {}
    t = sec.get("totals") if isinstance(sec.get("totals"), dict) else {}
    cloud = []
    for row in (sec.get("cloud") if isinstance(sec.get("cloud"), list) else []):
        if not isinstance(row, dict):
            continue
        cloud.append({
            "vendor": _str(row.get("vendor"), "?"),
            "model": _str(row.get("model"), "?"),
            "in_usd": _float(row.get("in_usd")),
            "cached_in_usd": _float(row.get("cached_in_usd")),
            "out_usd": _float(row.get("out_usd")),
            "note": _str(row.get("note"), ""),
            "est_usd": _float(row.get("est_usd")),
        })
    loc = sec.get("local") if isinstance(sec.get("local"), dict) else {}
    pw = sec.get("power") if isinstance(sec.get("power"), dict) else {}
    return {
        "updated_at": _str(sec.get("updated_at")) or now_iso(),
        "totals": {
            "prompt": _int(t.get("prompt")),
            "gen": _int(t.get("gen")),
            "cache_read": _int(t.get("cache_read")),
        },
        "cloud": cloud,
        "local": {
            "kwh": _float(loc.get("kwh")),
            "rub": _float(loc.get("rub")),
            "note": _str(loc.get("note")),
        },
        # константы «своего железа» — фронт пересчитывает ₽ на выбранный период
        "power": {
            "avg_w": _float(pw.get("avg_w"), 700.0),
            "rub_per_kwh": _float(pw.get("rub_per_kwh"), 10.0),
        },
    }


NORMALIZE = {"wire": normalize_wire, "server": normalize_server, "costs": normalize_costs}


# -------------------------------------------------------------- collectors ---

# Прогреть wire-секцию из пуша с Windows-машины (wire-логи физически только там).
# Кандидаты: локальный data/wire_push.json и ~/agent-tools/wire_push.json (куда
# кладёт пушер ops/push_wire.py). Свежее 7 суток — берём; старше — собираем самим.
WIRE_PUSH_CANDIDATES = (
    os.path.join(DATA_DIR, "wire_push.json"),
    os.path.expanduser("~/agent-tools/wire_push.json"),
)


def try_wire_push():
    import time as _t
    best = None
    for p in WIRE_PUSH_CANDIDATES:
        try:
            if os.path.isfile(p):
                m = os.path.getmtime(p)
                if best is None or m > best[1]:
                    best = (p, m)
        except OSError:
            pass
    if not best or _t.time() - best[1] > 7 * 86400:
        return None, None
    try:
        with open(best[0], encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return None, None
    if not isinstance(raw, dict) or "totals" not in raw:
        return None, None
    return raw, "push"


def run_collector(name: str, days: float):
    """(секция, статус) для collect_<name>.collect(days).

    Статус: "ok" | "fallback: <файл>" | "empty". Упавший коллектор —
    предыдущий data/*.json, иначе пустая секция по схеме.
    """
    mod_name = f"collect_{name}"
    argv_save = sys.argv
    try:
        sys.argv = [mod_name]  # подстраховка: модуль не должен видеть наши аргументы
        mod = importlib.import_module(mod_name)
        fn = getattr(mod, "collect", None)
        if not callable(fn):
            raise RuntimeError(f"в {mod_name}.py нет вызываемой collect(days)")
        try:
            raw = fn(days)
        except TypeError:
            raw = fn()  # коллектор проигнорировал days — зовём без аргументов
        if not isinstance(raw, dict):
            raise RuntimeError(f"{mod_name}.collect() вернул {type(raw).__name__}, ожидался dict")
        atomic_write(os.path.join(DATA_DIR, f"{mod_name}.json"),
                     json.dumps(raw, ensure_ascii=False))
        return raw, "ok"
    except Exception as exc:
        prev = load_fallback(name)
        if prev is not None:
            print(f"  [warn] {mod_name}: {exc!r} — беру прошлые data/*.json", file=sys.stderr)
            return prev, "fallback"
        print(f"  [warn] {mod_name}: {exc!r} — секция пуста", file=sys.stderr)
        return {}, "empty"
    finally:
        sys.argv = argv_save


# ------------------------------------------------------------------- main ---

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Собрать data.js для дашборда (CONTRACT.md).")
    ap.add_argument("--days", type=float, default=DEFAULT_DAYS,
                    help=f"окно сбора в днях (по умолчанию {DEFAULT_DAYS:g})")
    args = ap.parse_args(argv)
    days = args.days if args.days > 0 else 0.0  # 0 = полная история

    statuses = {}
    dash = {
        "generated_at": now_iso(),
        # null = полная история; конкретное N — если сбор ограничен окном
        "window_days": int(days) if float(days).is_integer() and days > 0 else None,
    }
    for name in SECTIONS:
        raw = status = None
        if name == "wire":
            raw, status = try_wire_push()
        elif name == "costs":
            # на сервере collect_wire нет (wire логи на Windows) — кормим ценами
            # из уже собранной wire-секции, иначе fallback на обычный сбор.
            # Кэш-долю для облачного биллинга берём серверную (замеренную на
            # стенде): клиентский inputCacheRead SGLang не отдаёт и врёт ~1%.
            try:
                import collect_costs
                wfc = dash["wire"]
                hit = (dash["server"].get("info") or {}).get("cache_hit_pct")
                tot = (dash["wire"].get("totals") or {})
                if hit is not None and tot.get("prompt"):
                    eff_cr = int(round(tot["prompt"] * min(max(hit, 0.0), 100.0) / 100.0))
                    wfc = dict(dash["wire"], totals=dict(tot, cache_read=eff_cr))
                raw = collect_costs.collect(days, wire_totals=wfc)
                status = "ok"
            except Exception:
                raw = status = None
        if status is None:
            raw, status = run_collector(name, days)
        dash[name] = NORMALIZE[name](raw)
        statuses[name] = status

    # «Коллектор отработал» != «статистика есть». Без этого переезд на https
    # тихо превращает серверную секцию в нули с надписью OK (14.10, 9 часов).
    if not dash["server"].get("online"):
        statuses["server"] = "degraded"

    atomic_write(OUT_PATH, "window.DASH = " + json.dumps(dash, ensure_ascii=False) + ";\n")
    size_kb = os.path.getsize(OUT_PATH) / 1024

    w, s, c = dash["wire"], dash["server"], dash["costs"]
    tag = lambda n: {"ok": "OK", "push": "PUSH", "fallback": "FALLBACK", "empty": "EMPTY"}.get(statuses[n], statuses[n].upper())
    print(f"[refresh] окно {'полное' if days <= 0 else f'{days:g}д'} | data.js {size_kb:.1f} KB | {dash['generated_at']}")
    print(f"[wire {tag('wire')}] steps={w['totals']['steps']:,} prompt={w['totals']['prompt']:,} "
          f"gen={w['totals']['gen']:,} cache_hit={w['totals']['cache_hit_pct']:.1f}% "
          f"проектов={len(w['projects'])} дней={len(w['days'])}")
    top = max(c["cloud"], key=lambda r: r["est_usd"], default=None)
    top_txt = f" топ={top['model']} ${top['est_usd']:.2f}" if top else ""
    print(f"[server {tag('server')}] online={s['online']} prompt_total={s['counters']['prompt_total']:,} "
          f"gen_total={s['counters']['generation_total']:,} | "
          f"[costs {tag('costs')}] облако={len(c['cloud'])}{top_txt} локально≈{c['local']['rub']:.0f} ₽")
    return 0


if __name__ == "__main__":
    sys.exit(main())
