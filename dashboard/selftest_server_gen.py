#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Самотест дневного ряда серверного gen throughput (collect_server, CONTRACT.md п.2).

Покрывает: парсер строки `docker logs penny` (только TP0 + Decode batch +
gen throughput), конверт UTC->MSK (+03:00) с прыжком через полночь, инкремент
в персистентные бакеты с дедупом по ts, merge бакетов (fallback), пустые сутки,
медиану/p95 из бакетов и сквозной цикл backfill -> инкремент -> «docker лёг».

Запуск:  python D:\\llm\\dashboard\\selftest_server_gen.py     (exit 0 = OK)
Только stdlib; сети и docker не трогает (docker мокается).
"""
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import collect_server as cs  # noqa: E402

UTC = timezone.utc


def logline(ts, tps, tp="TP0", msg="Decode batch"):
    return (f"[{ts} {tp}] {msg}, #running-req: 1, gen throughput (token/s): {tps}, "
            f"#queue-req: 0")


# ---------------------------------------------------------------- парсер ---
def t_parse_positive():
    p = cs.parse_gen_line(logline("2026-10-24 00:42:44", "175.60"))
    assert p is not None, "эталонная строка не распарсилась"
    ts, tps = p
    assert ts == datetime(2026, 10, 24, 0, 42, 44, tzinfo=UTC), ts
    assert ts.tzinfo is UTC
    assert abs(tps - 175.60) < 1e-9, tps


def t_parse_int_value():
    p = cs.parse_gen_line(logline("2026-10-24 00:42:44", "141"))
    assert p is not None and abs(p[1] - 141.0) < 1e-9, p


def t_parse_tp1_ignored():
    assert cs.parse_gen_line(logline("2026-10-24 00:42:44", "175.60", tp="TP1")) is None


def t_parse_prefill_ignored():
    assert cs.parse_gen_line(logline("2026-10-24 00:42:44", "175.60",
                                     msg="Prefill batch")) is None


def t_parse_other_throughput_ignored():
    # только prompt/output throughput, без «gen throughput (token/s):»
    line = ("[2026-10-24 00:42:44 TP0] Decode batch, #running-req: 1, "
            "prompt throughput (token/s): 999.00, #queue-req: 0")
    assert cs.parse_gen_line(line) is None


def t_parse_garbage():
    for junk in ("", "\n", "garbage",
                 "[2026-10-24 00:42:44 TP0] Decode batch, gen throughput (token/s): abc",
                 "[9999-13-99 00:00:00 TP0] Decode batch, gen throughput (token/s): 10"):
        assert cs.parse_gen_line(junk) is None, junk
    # за физическими границами — мусор формата
    assert cs.parse_gen_line(logline("2026-10-24 00:42:44", "0.0")) is None
    assert cs.parse_gen_line(logline("2026-10-24 00:42:44", "999999")) is None


# ------------------------------------------------------------ UTC -> MSK ---
def t_msk_same_day():
    assert cs.msk_date(datetime(2026, 10, 24, 20, 59, 59, tzinfo=UTC)) == "2026-10-24"


def t_msk_next_day():
    # 21:00 UTC = 00:00 MSK следующих суток — прыжок через полночь
    assert cs.msk_date(datetime(2026, 10, 24, 21, 0, 1, tzinfo=UTC)) == "2026-10-25"
    assert cs.msk_date(datetime(2026, 10, 24, 23, 59, 59, tzinfo=UTC)) == "2026-10-25"


def t_msk_month_rollover():
    assert cs.msk_date(datetime(2026, 10, 31, 21, 30, tzinfo=UTC)) == "2026-11-01"


# --------------------------------------------------------------- бакеты ----
def t_merge_basic():
    b = {}
    text = "\n".join([
        logline("2026-10-24 10:00:00", "100.0"),
        logline("2026-10-24 10:00:05", "100.4"),   # тот же бакет 100
        logline("2026-10-24 10:00:10", "101.0"),
        logline("2026-10-24 10:00:15", "102.0", tp="TP1"),  # дубль — мимо
        "[2026-10-24 10:00:20 TP0] Prefill batch, gen throughput (token/s): 50.0",  # мимо
        "полная мура",
    ])
    added, mx = cs.merge_log_lines(b, text)
    assert added == 3, added
    assert mx == datetime(2026, 10, 24, 10, 0, 10, tzinfo=UTC), mx
    assert b["2026-10-24"]["100"] == 2, b
    assert b["2026-10-24"]["101"] == 1, b


def t_merge_dedup_and_increment():
    b = {}
    text1 = "\n".join([logline("2026-10-24 10:00:00", "100.0"),
                       logline("2026-10-24 10:00:10", "110.0")])
    _, mx = cs.merge_log_lines(b, text1)
    # пересекшееся окно --since: старые строки не учитываются дважды
    text2 = "\n".join([logline("2026-10-24 10:00:00", "100.0"),
                       logline("2026-10-24 10:00:10", "110.0"),   # ts == max -> дедуп
                       logline("2026-10-24 10:00:20", "110.0")])  # новая — в дело
    added, mx2 = cs.merge_log_lines(b, text2, after_ts=mx)
    assert added == 1, added
    assert mx2 == datetime(2026, 10, 24, 10, 0, 20, tzinfo=UTC), mx2
    assert sum(b["2026-10-24"].values()) == 3, b


def t_merge_midnight_split():
    # 20:59:59 UTC -> 24.10 MSK; 21:00:01 UTC -> уже 25.10 MSK: одни сутки режутся ножом MSK
    b = {}
    text = "\n".join([logline("2026-10-24 20:59:59", "150.0"),
                      logline("2026-10-24 21:00:01", "180.0")])
    added, _ = cs.merge_log_lines(b, text)
    assert added == 2
    assert b["2026-10-24"]["150"] == 1 and b["2026-10-25"]["180"] == 1, b


def t_merge_buckets_fallback():
    dst = {"2026-10-24": {"100": 2}}
    src = {"2026-10-24": {"100": 1, "120": 1},
           "2026-10-25": {"90": 1},
           "bad_day": "не словарь",
           "2026-10-26": {"xx": 1, "100": -5, "101": "мало"}}
    cs.merge_buckets(dst, src)
    assert dst["2026-10-24"] == {"100": 3, "120": 1}, dst
    assert dst["2026-10-25"] == {"90": 1}, dst
    assert "2026-10-26" not in dst or not dst["2026-10-26"], dst
    assert dst.get("bad_day") in (None, {}), dst


# ------------------------------------------------- квантили/ряд из бакетов --
def t_p50_median_even():
    # [100,200] -> медиана 150 (линейная интерполяция, как statistics.median)
    rows = cs.gen_days_from_buckets({"2026-10-24": {"100": 1, "200": 1}})
    assert rows == [{"date": "2026-10-24", "n": 2, "p50": 150.0, "p95": 195.0}], rows


def t_p50_mode_bucket():
    # [100,200,200,200] -> p50=200; p95=200
    rows = cs.gen_days_from_buckets({"2026-10-24": {"100": 1, "200": 3}})
    assert rows[0]["n"] == 4 and rows[0]["p50"] == 200.0 and rows[0]["p95"] == 200.0, rows


def t_single_bucket_day():
    rows = cs.gen_days_from_buckets({"2026-10-24": {"150": 5}})
    assert rows == [{"date": "2026-10-24", "n": 5, "p50": 150.0, "p95": 150.0}], rows


def t_empty_days_skipped():
    buckets = {"2026-10-24": {},                       # пустые сутки
               "2026-10-25": {"100": 0},               # обнулённый бакет
               "2026-10-26": "мусор",
               "2026-10-27": {"100": 1}}
    rows = cs.gen_days_from_buckets(buckets)
    assert [r["date"] for r in rows] == ["2026-10-27"], rows


def t_days_sorted_and_shaped():
    buckets = {"2026-10-26": {"120": 2}, "2026-10-24": {"100": 1}, "2026-10-25": {"110": 1}}
    rows = cs.gen_days_from_buckets(buckets)
    assert [r["date"] for r in rows] == ["2026-10-24", "2026-10-25", "2026-10-26"], rows
    for r in rows:
        assert set(r.keys()) == {"date", "n", "p50", "p95"}, r
        assert isinstance(r["n"], int) and isinstance(r["p50"], float) and isinstance(r["p95"], float), r


# ------------------------------------------------- сквозной цикл с моком ----
def t_update_gen_days_cycle():
    """backfill (capped) -> инкремент (дедуп, окно) -> docker лёг: ряд из бакетов."""
    tmp = Path(tempfile.mkdtemp(prefix="dash_gen_selftest_"))
    old_b, old_s = cs.GEN_BUCKETS_PATH, cs.GEN_STATE_PATH
    old_l, old_dl = cs.GEN_LOCK_PATH, cs._docker_logs
    calls = []
    try:
        cs.GEN_BUCKETS_PATH = tmp / "server_gen_buckets.json"
        cs.GEN_STATE_PATH = tmp / "server_gen_state.json"
        cs.GEN_LOCK_PATH = tmp / "server_gen.lock"

        full = "\n".join([logline("2026-10-24 10:00:00", "100.0"),
                          logline("2026-10-24 10:00:10", "110.0"),
                          logline("2026-10-25 10:00:00", "120.0")])

        def docker_first(since, backfill=False):
            calls.append((since, backfill))
            return full  # первый заход: backfill=True с ограниченным --since

        cs._docker_logs = docker_first
        rows = cs._update_gen_days()
        assert calls and calls[0][1] is True and calls[0][0] is not None, calls
        assert [r["date"] for r in rows] == ["2026-10-24", "2026-10-25"], rows
        assert rows[0]["n"] == 2 and rows[1]["n"] == 1, rows
        st = cs._load_json_dict(cs.GEN_STATE_PATH)
        assert st.get("backfilled") is True, st
        assert st.get("max_ts_utc") == "2026-10-25T10:00:00+00:00", st

        def docker_inc(since, backfill=False):
            calls.append((since, backfill))
            # хвост с перекрытием: старая строка + одна новая (старые режутся дедупом)
            return logline("2026-10-25 10:00:00", "120.0") + "\n" + logline("2026-10-25 11:00:00", "130.0")

        cs._docker_logs = docker_inc
        rows = cs._update_gen_days()
        assert calls[-1][1] is False and calls[-1][0] is not None, "инкремент с --since, не backfill"
        assert rows[1]["n"] == 2, rows  # 120 не задублировался, пришёл 130
        st = cs._load_json_dict(cs.GEN_STATE_PATH)
        assert st.get("max_ts_utc") == "2026-10-25T11:00:00+00:00", st

        def docker_down(since, backfill=False):
            calls.append((since, backfill))
            return None  # docker недоступен — не падаем, ряд из прошлых бакетов

        cs._docker_logs = docker_down
        rows2 = cs._update_gen_days()
        assert rows2 == rows, (rows2, rows)
        assert cs.GEN_BUCKETS_PATH.exists()
    finally:
        cs.GEN_BUCKETS_PATH, cs.GEN_STATE_PATH = old_b, old_s
        cs.GEN_LOCK_PATH, cs._docker_logs = old_l, old_dl


def t_container_validator():
    """env-инъекция имени контейнера отбивается; валидное имя проходит."""
    assert cs._valid_container("penny") is True
    assert cs._valid_container("penny-2") is True
    assert cs._valid_container("a.b_c-d") is True
    for bad in ("", "-f", "--tail=all", " penny", "pen\nny", "x" * 65, ".hidden"):
        assert cs._valid_container(bad) is False, bad


def t_window_capped_on_idle():
    """Простой декода: окно --since не растягивается за GEN_WINDOW_CAP_H."""
    tmp = Path(tempfile.mkdtemp(prefix="dash_gen_cap_"))
    old_b, old_s = cs.GEN_BUCKETS_PATH, cs.GEN_STATE_PATH
    old_l, old_dl = cs.GEN_LOCK_PATH, cs._docker_logs
    try:
        cs.GEN_BUCKETS_PATH = tmp / "b.json"
        cs.GEN_STATE_PATH = tmp / "s.json"
        cs.GEN_LOCK_PATH = tmp / "l.lock"
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        st = {"backfilled": True,
              "max_ts_utc": (now - timedelta(hours=48)).isoformat(),
              "last_run_utc": (now - timedelta(minutes=30)).isoformat()}
        cs._atomic_write_json(cs.GEN_STATE_PATH, st)
        cs._atomic_write_json(cs.GEN_BUCKETS_PATH, {})
        seen = []

        def caplog(since, backfill=False):
            seen.append(since)
            return ""

        cs._docker_logs = caplog
        cs._update_gen_days()
        since_dt = datetime.strptime(seen[0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        assert (datetime.now(timezone.utc) - since_dt).total_seconds() <= cs.GEN_WINDOW_CAP_H * 3600 + 120, seen
    finally:
        cs.GEN_BUCKETS_PATH, cs.GEN_STATE_PATH = old_b, old_s
        cs.GEN_LOCK_PATH, cs._docker_logs = old_l, old_dl


def t_buckets_deleted_rebackfills():
    """Бакеты удалили, state жив -> backfilled сбрасывается, ряд перечитывается."""
    tmp = Path(tempfile.mkdtemp(prefix="dash_gen_rebak_"))
    old_b, old_s = cs.GEN_BUCKETS_PATH, cs.GEN_STATE_PATH
    old_l, old_dl = cs.GEN_LOCK_PATH, cs._docker_logs
    try:
        cs.GEN_BUCKETS_PATH = tmp / "b.json"
        cs.GEN_STATE_PATH = tmp / "s.json"
        cs.GEN_LOCK_PATH = tmp / "l.lock"
        cs._atomic_write_json(cs.GEN_STATE_PATH,
                              {"backfilled": True, "max_ts_utc": None, "last_run_utc": None})
        calls = []

        def docker(since, backfill=False):
            calls.append(backfill)
            return logline("2026-10-26 09:00:00", "150.0")

        cs._docker_logs = docker
        rows = cs._update_gen_days()
        assert calls[0] is True, "без бакетов обязан быть backfill, а не хвост"
        assert rows and rows[0]["date"] == "2026-10-26", rows
    finally:
        cs.GEN_BUCKETS_PATH, cs.GEN_STATE_PATH = old_b, old_s
        cs.GEN_LOCK_PATH, cs._docker_logs = old_l, old_dl


def t_lock_busy_skips_tick():
    """Занятый flock => docker не зовём, файлы не пишем, ряд из прошлых бакетов."""
    import sys, types
    tmp = Path(tempfile.mkdtemp(prefix="dash_gen_lock_"))
    old_b, old_s = cs.GEN_BUCKETS_PATH, cs.GEN_STATE_PATH
    old_l, old_dl = cs.GEN_LOCK_PATH, cs._docker_logs
    old_fcntl = sys.modules.get("fcntl")
    try:
        cs.GEN_BUCKETS_PATH = tmp / "b.json"
        cs.GEN_STATE_PATH = tmp / "s.json"
        cs.GEN_LOCK_PATH = tmp / "l.lock"
        cs._atomic_write_json(cs.GEN_BUCKETS_PATH, {"2026-10-26": {"150": 3}})

        fake = types.ModuleType("fcntl")
        fake.LOCK_EX = 2
        fake.LOCK_NB = 4

        class _Busy(OSError):
            pass

        def flock_fail(*a, **k):
            raise _Busy("locked")

        fake.flock = flock_fail
        sys.modules["fcntl"] = fake
        calls = []

        def spy_docker(since, backfill=False):
            calls.append(since)
            return logline("2026-10-27 09:00:00", "999.0")

        cs._docker_logs = spy_docker
        rows = cs._update_gen_days()
        assert calls == [], "при занятом локе docker дёргать нельзя"
        assert [r["date"] for r in rows] == ["2026-10-26"], rows
        assert not cs.GEN_STATE_PATH.exists(), "state трогать нельзя"
    finally:
        if old_fcntl is not None:
            sys.modules["fcntl"] = old_fcntl
        else:
            sys.modules.pop("fcntl", None)
        cs.GEN_BUCKETS_PATH, cs.GEN_STATE_PATH = old_b, old_s
        cs.GEN_LOCK_PATH, cs._docker_logs = old_l, old_dl


# ---------------------------------------------------------------- runner ---
def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("t_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {name}: {e}")
        except Exception as e:
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"selftest_server_gen: {len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
