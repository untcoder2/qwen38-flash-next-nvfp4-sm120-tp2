#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""collect_costs.py — агент collector_costs: модуль цен и сравнения «облако vs своё железо».

Контракт: D:\\llm\\dashboard\\CONTRACT.md (п. «Источники данных» п.3, ключ схемы `costs`).

collect(days) возвращает блок costs:
  totals — wire-тоталы {prompt, gen, cache_read};
  cloud  — оценка по каждому вендору: est_usd = cache_read/1e6*cached_in
           + (prompt-cache_read)/1e6*in + gen/1e6*out;
  local  — электричество: kwh = hours_loaded * 700 W / 1000, rub = kwh * 10 ₽.

Иерархия источников wire-данных (никогда не падает):
  1. wire_totals=... — уже посчитанные тоталы, переданные вызывающим (refresh.py / glue);
  2. import collect_wire -> collect_wire.collect(days) — если модуль уже существует;
  3. data/wire.json — если его уже писал collect_wire;
  4. нули.

Тест по контракту: `python D:\\llm\\dashboard\\collect_costs.py` → пишет data\\costs.json,
валидный JSON, summary на 1 строку, не падает на реальных данных (30 дней по умолчанию).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

# ---------------------------------------------------------------------------
# Облачные прайсы, USD за 1M токенов. Актуальный модельный ряд, сверено с
# живыми прайс-страницами/агрегаторами 2026-10-11 (BenchLM / OpenRouter /
# pricepertoken / coCode Zen / llm-stats / economize.cloud и пр.). cached_in
# с плашкой «~» — оценка ~10% от входа, когда вендор не публикует кэш-тариф
# в агрегаторах; реальный кэш обычно ещё дешевле.
# ---------------------------------------------------------------------------
PRICES = [
    # vendor, model, in / cached_in / out за 1M токенов (USD), проверено 11.10.26
    {"vendor": "OpenAI",      "model": "GPT-6 Astra",       "in_usd": 10.00, "cached_in_usd": 1.00,  "out_usd": 50.00, "note": "флагмен-линейка OpenAI; cache-write $12.50; >272K: 2x/1.5x"},
    {"vendor": "OpenAI",      "model": "GPT-6.1 Sol",       "in_usd": 2.00,  "cached_in_usd": 0.10,  "out_usd": 10.00, "note": "текущий флагман открытого API (DevDay 29.09); GPT-6.1 Astra отменена; cache-write $2.50; >272K: $4/$0.20/$15"},
    {"vendor": "OpenAI",      "model": "GPT-6 Sol",         "in_usd": 2.00,  "cached_in_usd": 0.20,  "out_usd": 10.00, "note": "прежний Sol: кэш вдвое дороже 6.1; >272K: 2x/1.5x"},
    {"vendor": "OpenAI",      "model": "GPT-6 Luna",        "in_usd": 0.10,  "cached_in_usd": 0.01,  "out_usd": 0.50,  "note": "бюджетный класс"},
    {"vendor": "Anthropic",   "model": "Claude Fable 5",    "in_usd": 10.00, "cached_in_usd": 1.00,  "out_usd": 50.00, "note": "legacy: кэш-риды $1.00 (у 5.1 — $0.25); cache-write $12.50"},
    {"vendor": "Anthropic",   "model": "Claude Fable 5.1",  "in_usd": 10.00, "cached_in_usd": 0.25,  "out_usd": 50.00, "note": "запущен 01.09.26; кэш-риды урезаны с $1.00; типичные Fable-нагрузки ~−25%"},
    {"vendor": "Anthropic",   "model": "Claude Opus 5.5",   "in_usd": 4.00,  "cached_in_usd": 0.20,  "out_usd": 20.00, "note": "Opus 5.5 (22.09.26); cache-write $5"},
    {"vendor": "Anthropic",   "model": "Claude Sonnet 5.5", "in_usd": 2.00,  "cached_in_usd": 0.10,  "out_usd": 10.00, "note": "сменил Sonnet 5; кэш-риды упали $0.20→$0.10 (07.10.26)"},
    {"vendor": "Anthropic",   "model": "Claude Haiku 5.5",  "in_usd": 0.10,  "cached_in_usd": 0.01,  "out_usd": 0.50,  "note": "с 07.10.26, в 10 раз дешевле Haiku 4.5; промпт >100K: $0.50/$0.05/$2.50"},
    {"vendor": "Moonshot AI", "model": "Kimi K3",           "in_usd": 3.00,  "cached_in_usd": 0.30,  "out_usd": 15.00, "note": "официальный прайс Moonshot (1M контекста, input видео/картинки)"},
    {"vendor": "Moonshot AI", "model": "Kimi K2.7 Code",    "in_usd": 0.95,  "cached_in_usd": 0.19,  "out_usd": 4.00,  "note": "агентский кодинг; кэш-хит официально $0.19"},
    {"vendor": "xAI",         "model": "Grok 4.7",          "in_usd": 2.00,  "cached_in_usd": 0.50,  "out_usd": 6.00,  "note": "промпт >200K: $4/$1/$12"},
    {"vendor": "Google",      "model": "Gemini 3.1 Pro",    "in_usd": 2.00,  "cached_in_usd": 0.20,  "out_usd": 12.00, "note": "промпт >200K: $4/$18; кэш −90%"},
    {"vendor": "Google",      "model": "Gemini 3.8 Flash",  "in_usd": 0.75,  "cached_in_usd": 0.075, "out_usd": 3.75,  "note": "интро-цена до 31.12.26, с 01.01.27 вдвое; кэш ~"},
    {"vendor": "Google",      "model": "Gemini 2.5 Flash-Lite", "in_usd": 0.10, "cached_in_usd": 0.01, "out_usd": 0.40, "note": "самый дешёвый capable (в прайсе ещё 3.5 FL: $0.30/$0.03/$2.50)"},
    {"vendor": "DeepSeek",    "model": "V4.1 Flash",        "in_usd": 0.30,  "cached_in_usd": 0.006, "out_usd": 1.20,  "note": "пик-тариф; офф-пик $0.15/$0.003/$0.60; сменил V4 Pro 10.09"},
    {"vendor": "Alibaba",     "model": "Qwen3.8-Max",       "in_usd": 2.00,  "cached_in_usd": 0.25,  "out_usd": 6.00,  "note": "флагман Alibaba Cloud (SG-region); кэш опубликован $0.25"},
    {"vendor": "Alibaba",     "model": "Qwen3.8-Flash",     "in_usd": 0.15,  "cached_in_usd": 0.016, "out_usd": 0.47,  "note": "≈ та же модель, что на своём стенде; кэш $0.016 (08.10)"},
    {"vendor": "Z.ai",        "model": "GLM-5.3",           "in_usd": 1.40,  "cached_in_usd": 0.26,  "out_usd": 4.40,  "note": "флагман Zhipu с 14.08.26; кэш-риды $0.26 (22.09, 08.10)"},
]

PRICE_NOTE = ("Цены облачников — живые прайс-страницы/агрегаторы, сверено 2026-10-11 "
              "(BenchLM 08–10.10, OpenRouter/CoCode Zen 06–08.10, llm-stats/economize 08–10.10); "
              "кэш-вход с «~» — оценка ~10% от входа. Могут отставать от прайса "
              "вендора и не учитывать пик/офф-пик и ступени контекста — сверяйте.")

# --- своё железо: хост инфера (molochko@192.168.2.127) ---
AVG_POWER_W = 700        # 2×RTX PRO 6000, усреднённое потребление под нагрузкой (диапазон 600–750)
RUB_PER_KWH = 10.0
HARDWARE_NOTE = "2×RTX PRO 6000, ~700 Вт суммарно под нагрузкой (600–750 Вт)"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _to_int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _wire_from_argument(wire_totals: dict) -> dict | None:
    """Вызывающий (glue/refresh) уже посчитал wire-тоталы — берём как есть."""
    return wire_totals if isinstance(wire_totals, dict) else None


def _wire_from_import(days: float) -> dict | None:
    """Пробуем импортировать collect_wire (пишет другой агент) и позвать collect()."""
    try:
        import collect_wire  # noqa: PLC0415 — opportunistic import by design
    except Exception:
        return None
    try:
        res = collect_wire.collect(days)
    except Exception:
        return None
    return res if isinstance(res, dict) else None


def _wire_from_json() -> dict | None:
    """Fallback: data/wire.json, если его уже оставил после себя collect_wire."""
    try:
        raw = (DATA_DIR / "wire.json").read_text(encoding="utf-8")
        obj = json.loads(raw)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _extract_totals(src: dict) -> tuple[int, int, int]:
    """Достаёт prompt/gen/cache_read из блока wire (или из «голых» тоталов)."""
    holder = src.get("totals") if isinstance(src.get("totals"), dict) else src
    prompt = _to_int(holder.get("prompt"))
    gen = _to_int(holder.get("gen"))
    cache_read = min(_to_int(holder.get("cache_read")), prompt)
    return prompt, gen, cache_read


def _extract_days(src: dict) -> list | None:
    days = src.get("days")
    return days if isinstance(days, list) and days else None


def _cloud_rows(prompt: int, gen: int, cache_read: int) -> list[dict]:
    rows = []
    for p in PRICES:
        est = (cache_read / 1e6 * p["cached_in_usd"]
               + (prompt - cache_read) / 1e6 * p["in_usd"]
               + gen / 1e6 * p["out_usd"])
        rows.append({
            "vendor": p["vendor"],
            "model": p["model"],
            "in_usd": p["in_usd"],
            "cached_in_usd": p["cached_in_usd"],
            "out_usd": p["out_usd"],
            "note": p.get("note", ""),
            "est_usd": round(est, 2),
        })
    rows.sort(key=lambda r: (-r["est_usd"], r["vendor"], r["model"]))  # дорогие сверху
    return rows


def collect(days: float = 0.0, wire_totals: dict | None = None) -> dict:
    """Блок `costs` по контракту. days — окно в днях (0 = полная история);
    wire_totals — готовые тоталы {prompt, gen, cache_read} (опционально с `days`
    для загрузки железа)."""
    src = _wire_from_argument(wire_totals)
    wire_source = "argument"
    if src is None:
        src = _wire_from_import(days)
        wire_source = "collect_wire"
    if src is None:
        src = _wire_from_json()
        wire_source = "data/wire.json"
    if src is None:
        wire_source = "zeros"

    prompt, gen, cache_read = (0, 0, 0)
    day_entries = None
    if src is not None:
        prompt, gen, cache_read = _extract_totals(src)
        day_entries = _extract_days(src)

    # Загрузка железа: часы из wire-дней (активные дни × 24), иначе 24/7 за окно.
    if day_entries:
        active = sum(
            1 for d in day_entries
            if isinstance(d, dict) and (_to_int(d.get("steps")) > 0
                                        or _to_int(d.get("prompt")) + _to_int(d.get("gen")) > 0)
        )
        hours_loaded = active * 24.0
        hours_note = f"активных дней в wire: {active} из {len(day_entries)} × 24 ч"
    else:
        hours_loaded = max(float(days), 1.0) * 24.0
        hours_note = f"оценка 24/7 за окно {days:g} дн."
    kwh = hours_loaded * AVG_POWER_W / 1000.0
    rub = kwh * RUB_PER_KWH

    return {
        "updated_at": _now_iso(),
        "window_days": days,
        "wire_source": wire_source,
        "totals": {"prompt": prompt, "gen": gen, "cache_read": cache_read},
        "cloud": _cloud_rows(prompt, gen, cache_read),
        "local": {
            "kwh": round(kwh, 1),
            "rub": round(rub),
            "note": f"{HARDWARE_NOTE}: {hours_note} = {hours_loaded:g} ч; "
                    f"тариф {RUB_PER_KWH:g} ₽/кВт·ч.",
        },
        "note": PRICE_NOTE,
    }


# ---------------------------------------------------------------------------
# CLI / summary
# ---------------------------------------------------------------------------
def _grp(x: float, digits: int = 0) -> str:
    """Число с разделителем тысяч (узкий пробел не нужен — обычный пробел)."""
    return f"{x:,.{digits}f}".replace(",", " ")


def summary_line(out: dict) -> str:
    cloud = out["cloud"]
    cheap, dear = cloud[-1], cloud[0]
    t = out["totals"]
    loc = out["local"]
    return (
        f"costs [{out['window_days']:g} д, wire={out['wire_source']}, "
        f"prompt={_grp(t['prompt'])}, gen={_grp(t['gen'])}]: "
        f"облако min ${_grp(cheap['est_usd'], 2)} ({cheap['vendor']} {cheap['model']}) | "
        f"max ${_grp(dear['est_usd'], 2)} ({dear['vendor']} {dear['model']}) | "
        f"локально {_grp(loc['kwh'], 1)} кВт·ч ≈ {_grp(loc['rub'])} ₽"
    )


def main(argv: list[str] | None = None) -> dict:
    argv = sys.argv[1:] if argv is None else list(argv)
    days = 0.0  # по умолчанию полная история (0 = без среза)
    if argv:
        try:
            days = float(argv[0])
        except ValueError:
            print(f"[costs] не понял аргумент '{argv[0]}', беру полное окно", file=sys.stderr)
    out = collect(days)
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / "costs.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"[costs] не записал data/costs.json: {exc}", file=sys.stderr)
    print(summary_line(out))
    return out


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    main()
