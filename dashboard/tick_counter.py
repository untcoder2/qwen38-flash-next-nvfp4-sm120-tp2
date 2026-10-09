#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тик накопительных серверных счётчиков: снимает metrics и свёртывает baseline
в data/server_state.json. Ставится в cron раз в 30 минут, чтобы рестарт инфера
"съедал" не больше получаса накрутки. Полный rebuild data.js — refresh.py."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect_server  # noqa: E402


def main() -> int:
    snap = collect_server.collect()
    c = snap.get("counters") or {}
    i = snap.get("info") or {}
    base = i.get("counters_baseline") or {}
    print(f"tick online={snap.get('online')} total_prompt={c.get('prompt_total', 0):,} "
          f"total_gen={c.get('generation_total', 0):,} baseline_prompt={base.get('prompt_total', 0):,}")
    return 0 if snap.get("online") else 1


if __name__ == "__main__":
    raise SystemExit(main())
