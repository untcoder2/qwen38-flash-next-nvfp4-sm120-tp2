#!/usr/bin/env python3
"""Коллектор метрик инференс-сервера (SGLang) для токенов-дашборда.

Контракт: CONTRACT.md п.2 (источник "Сервер инфера"), ключ схемы "server":
    { updated_at: iso, online: bool,
      counters: { prompt_total, generation_total },   # int, сумма ВСЕХ серий
      info: { max_total_num_tokens, ... },            # что реально удалось вытянуть
      gen_tps_days: [ {date, n, p50, p95} ] }         # дневной ряд gen throughput
                                                     # движка (docker logs penny)

Запросы: http://192.168.2.127:1234, timeout 8 c.
Обрыв сети/таймаут => online:false, пустые counters, исключений наружу не выпускаем.

Имена полей проверены по фактическим ответам живого сервера 2026-10-09:
  /metrics        — sglang:prompt_tokens_total / sglang:generation_tokens_total
                    (серии по is_streaming), гейджи пула KV: token_usage,
                    full_token_usage, swa_token_usage, mamba_usage,
                    kv_available_tokens, kv_cache_memory_usage_gb (по tp_rank);
  /get_server_info — плоский dict ~490 конфиг-ключей; token_usage/memory_usage
                    в нём ОТСУТСТВУЮТ (не выдумываем), есть max_total_num_tokens,
                    page_size, kv_cache_dtype, hicache_size, max_running_requests.
"""
from __future__ import annotations

import json
import os
import re
import ssl
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = os.environ.get("DASH_SERVER_BASE", "https://192.168.2.127:1234")
TIMEOUT_S = 8.0
HERE = Path(__file__).resolve().parent

# Инфер с 14.10 отвечает только по https, лист самоподписанный (QwenHome-CA).
# На стенде корень лежит здесь же; перебивается DASH_CACERT (на Windows —
# certs/qwen-home-ca.pem из репозитория).
CA_FILE = Path(os.environ.get("DASH_CACERT", str(Path.home() / "penny/tls/ca.pem")))

# Prometheus-счётчики: суммируем СЕРИИ каждого по всем лейблам (контракт п.2)
COUNTER_SERIES = {
    "sglang:prompt_tokens_total": "prompt_total",
    "sglang:generation_tokens_total": "generation_total",
    "sglang:uncached_prompt_tokens_histogram_sum": "uncached_prompt_total",
}

# Гейджи KV-пула из /metrics: имя -> (ключ в info, как сводить серии)
# sum — складываем по tp_rank (GB/число токенов), max — берём худшую долю.
KV_GAUGES = {
    "sglang:token_usage": ("token_usage", max),
    "sglang:full_token_usage": ("full_token_usage", max),
    "sglang:swa_token_usage": ("swa_token_usage", max),
    "sglang:mamba_usage": ("mamba_usage", max),
    "sglang:kv_available_tokens": ("kv_available_tokens", sum),
    "sglang:kv_cache_memory_usage_gb": ("kv_cache_memory_gb", sum),
}

# Скалярные поля /get_server_info; включаем только те, что реально пришли (не None).
INFO_FIELDS = (
    "max_total_num_tokens",
    "max_total_tokens",
    "token_usage",
    "memory_usage",
    "page_size",
    "max_running_requests",
    "kv_cache_dtype",
    "hicache_size",
)

_METRIC_LINE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)"   # имя метрики
    r"(?:\{[^{}]*\})?"                        # необязательные лейблы
    r"\s+(?P<value>[-+0-9.eE]+|NaN|Inf|-Inf)"  # значение
    r"(?:\s+\d+)?\s*$"                        # необязательный timestamp
)


def _ssl_context() -> ssl.SSLContext | None:
    """None => схема http, контекст не нужен."""
    if not BASE.startswith("https"):
        return None
    ctx = ssl.create_default_context()
    if CA_FILE.exists():
        ctx.load_verify_locations(cafile=str(CA_FILE))
    return ctx  # без корня — проверка по системному доверию; self-signed упадёт громко


_SSL_CTX = _ssl_context()


def _fetch(path: str) -> str:
    req = urllib.request.Request(
        BASE + path, headers={"Accept": "text/plain, application/json"}
    )
    kwargs: dict = {"timeout": TIMEOUT_S}
    if _SSL_CTX is not None:
        kwargs["context"] = _SSL_CTX
    with urllib.request.urlopen(req, **kwargs) as resp:
        return resp.read().decode("utf-8", "replace")


def _num(raw: str) -> float | None:
    try:
        v = float(raw)
    except ValueError:
        return None
    return v if v == v and abs(v) != float("inf") else None  # NaN/Inf -> None


def _parse_metrics(text: str) -> tuple[dict, dict]:
    """(counters, kv) из Prometheus-текста; суммы по всем сериям, int для счётчиков."""
    acc: dict[str, list[float]] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = _METRIC_LINE.match(line)
        if not m:
            continue
        name, value = m["name"], _num(m["value"])
        if name not in COUNTER_SERIES and name not in KV_GAUGES or value is None:
            continue
        acc.setdefault(name, []).append(value)

    counters = {}
    for metric, key in COUNTER_SERIES.items():
        if metric in acc:
            counters[key] = int(round(sum(acc[metric])))

    kv = {}
    for metric, (key, reduce_) in KV_GAUGES.items():
        if metric in acc:
            kv[key] = round(reduce_(acc[metric]), 6)

    return counters, kv


def _parse_server_info(text: str) -> dict:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    info = {}
    for field in INFO_FIELDS:
        value = data.get(field)
        if value is None or isinstance(value, (dict, list)):
            continue  # поля может не быть — молча пропускаем
        info[field] = value
    return info


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# --- накопительные счётчики через рестарты -------------------------------------
# sglang обнуляет prompt/generation_total при рестарте инфера. Держим на диске
# baseline: когда видим новый контейнер (docker StartedAt поменялся) или падение
# счётчика — свёртываем последние значения в baseline. Итог = baseline + текущий
# бут. Тик tick_counter.py раз в 30 мин ограничивает потерю при рестарте ~30 мин.

STATE_PATH = HERE / "data" / "server_state.json"


def _boot_id():
    """Идентичность живого контейнера penny (только на хосте, где есть docker)."""
    try:
        import subprocess
        r = subprocess.run(
            ["docker", "inspect", "penny",
             "--format", "{{.Id}} {{.State.StartedAt}}"],
            capture_output=True, text=True, timeout=8)
        s = (r.stdout or "").strip()
        return s or None
    except Exception:
        return None


def _load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(st: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, STATE_PATH)


def _fold(counters: dict) -> tuple[dict, dict, dict]:
    """(итоговые, since_boot, baseline); состояние — атомарно под flock."""
    lock_f = None
    try:
        import fcntl
        lock_f = open(STATE_PATH.with_name("server_state.lock"), "w")
        fcntl.flock(lock_f, fcntl.LOCK_EX)
    except (ImportError, OSError):
        lock_f = None  # Windows/отказ — работаем без блокировки
    try:
        st = _load_state()
        base = {k: int(v) for k, v in (st.get("baseline") or {}).items()
                if isinstance(v, (int, float))}
        last = {k: int(v) for k, v in (st.get("last") or {}).items()
                if isinstance(v, (int, float))}
        boot = _boot_id()
        cur = int(counters.get("prompt_total", 0) or 0)
        restarted = bool(
            (boot and st.get("boot_id") and boot != st["boot_id"])
            or (cur > 0 and "prompt_total" in last and cur < last["prompt_total"])
        )
        if restarted and last:
            for k, v in last.items():
                base[k] = base.get(k, 0) + v
        since_boot = {k: int(v) for k, v in counters.items()}
        _save_state({"boot_id": boot, "last": since_boot,
                     "baseline": base, "updated_at": _now_iso()})
        total = {k: since_boot.get(k, 0) + base.get(k, 0)
                 for k in set(since_boot) | set(base)}
        return total, since_boot, base
    finally:
        if lock_f:
            try:
                lock_f.close()
            except OSError:
                pass


# --- дневной ряд серверного gen throughput (docker logs penny) ------------------
# SGLang пишет в stdout контейнера (метки UTC):
#   [2026-10-24 00:42:44 TP0] Decode batch, #running-req: 1, ... \
#       gen throughput (token/s): 175.60, #queue-req: 0
# TP1 дублирует TP0 — берём только TP0. История docker logs теряется при рестарте
# контейнера, поэтому бакеты персистентны: data/server_gen_buckets.json —
#   { "YYYY-MM-DD": { "<t/s целое>": count } }        (ключ дня — MSK +03:00 фикс.,
#                                                      та же дневная ось, что wire.days)
# data/server_gen_state.json — {backfilled, max_ts_utc, last_run_utc}.
# Каждый collect(): первый раз — полный backfill логов, дальше — docker logs
# --since (max_ts − 60 c) c дедупом по ts. Нет docker / ошибка — бакеты не трогаем,
# ряд из прошлых бакетов (graceful degradation, не падаем).

GEN_BUCKETS_PATH = HERE / "data" / "server_gen_buckets.json"
GEN_STATE_PATH = HERE / "data" / "server_gen_state.json"
GEN_LOCK_PATH = HERE / "data" / "server_gen.lock"
GEN_CONTAINER_RAW = os.environ.get("DASH_GEN_CONTAINER", "penny")


def _valid_container(name):
    """Имя контейнера без риска option-injection: начинается с буквы/цифры,
    дальше [A-Za-z0-9_.-], <=64. Пустая строка => docker не зовём."""
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name or ""))


GEN_CONTAINER = GEN_CONTAINER_RAW if _valid_container(GEN_CONTAINER_RAW) else ""
GEN_LOG_TIMEOUT_S = 30.0        # инкрементальный хвост логов
GEN_BACKFILL_TIMEOUT_S = 120.0  # первый прогон; НИЖЕ REFRESH_TIMEOUT_S(=180) у сервера,
                                # чтобы не зациклить повторяющийся backfill
GEN_BACKFILL_WINDOW_D = 30      # backfill capped: не жрём неограниченный docker logs в RAM
GEN_WINDOW_CAP_H = 6            # инкремент не растягивается в периоды простоя декода
GEN_OVERLAP_S = 60              # буфер --since; дубли режутся дедупом по max_ts
GEN_TPS_MIN, GEN_TPS_MAX = 0.5, 20000.0  # физ. границы: мусор/разрыв формата

_TZ_MSK = timezone(timedelta(hours=3))

_GEN_LINE = re.compile(
    r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) TP0\]"
    r".*Decode batch,.*gen throughput \(token/s\): (\d+(?:\.\d+)?)"
)


def parse_gen_line(line: str):
    """Строка docker logs -> (datetime UTC, tps) | None.

    Только TP0 (TP1 — дубль), только Decode batch, значение из
    «gen throughput (token/s):». Метка времени в строке — UTC."""
    m = _GEN_LINE.search(line)
    if not m:
        return None
    try:
        ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        tps = float(m.group(2))
    except ValueError:
        return None
    if not GEN_TPS_MIN <= tps <= GEN_TPS_MAX:
        return None
    return ts, tps


def msk_date(ts: datetime) -> str:
    """UTC-метка -> ключ дня дашборда (MSK, фикс. +03:00 — DST нет)."""
    return ts.astimezone(_TZ_MSK).strftime("%Y-%m-%d")


def merge_log_lines(buckets: dict, text, after_ts: datetime | None = None):
    """Инкремент строк логов в бакеты in-place -> (добавлено, макс_ts_в_тексте).

    after_ts — дедуп пересечения окна `--since`: строки с ts <= after_ts уже
    учтены в предыдущих сборах."""
    added = 0
    mx = None
    for line in text.splitlines():
        if "TP0" not in line or "Decode batch" not in line:
            continue
        parsed = parse_gen_line(line)
        if not parsed:
            continue
        ts, tps = parsed
        if mx is None or ts > mx:
            mx = ts
        if after_ts is not None and ts <= after_ts:
            continue
        day = buckets.setdefault(msk_date(ts), {})
        key = str(int(round(tps)))
        day[key] = day.get(key, 0) + 1
        added += 1
    return added, mx


def merge_buckets(dst: dict, src: dict) -> None:
    """Суммирование бакетов src в dst (in-place); мусорные ключи/счётчики пропускаем."""
    for date, day in (src or {}).items():
        if not isinstance(day, dict):
            continue
        d = dst.setdefault(str(date), {})
        for b, c in day.items():
            try:
                bi, ci = int(float(b)), int(float(c))
            except (TypeError, ValueError):
                continue
            if bi >= 0 and ci > 0:
                d[str(bi)] = d.get(str(bi), 0) + ci


def _value_at_rank(values, counts, rank: int) -> float:
    cum = 0
    for v, c in zip(values, counts):
        if rank < cum + c:
            return float(v)
        cum += c
    return float(values[-1])


def percentile_from_buckets(values, counts, q: float):
    """Квантиль q по сгруппированным значениям (values отсортированы, целые t/s).

    Линейная интерполяция между соседними порядковыми статистиками — при q=0.5
    совпадает со statistics.median; точность ограничена гранулярностью бакета."""
    n = sum(counts)
    if n <= 0:
        return None
    t = (n - 1) * q
    i = int(t)
    lo = _value_at_rank(values, counts, i)
    hi = lo if i + 1 > n - 1 else _value_at_rank(values, counts, i + 1)
    return lo + (hi - lo) * (t - i)


def gen_days_from_buckets(buckets: dict) -> list:
    """[{date, n, p50, p95}] по возрастанию дат; пустые сутки — не строка ряда."""
    norm = {str(k): v for k, v in (buckets or {}).items()}
    out = []
    for date in sorted(norm):
        day = norm[date]
        if not isinstance(day, dict):
            continue
        agg: dict[int, int] = {}
        for b, c in day.items():
            try:
                bi, ci = int(float(b)), int(float(c))
            except (TypeError, ValueError):
                continue
            if bi >= 0 and ci > 0:
                agg[bi] = agg.get(bi, 0) + ci
        n = sum(agg.values())
        if not n:
            continue
        vals = sorted(agg)
        counts = [agg[v] for v in vals]
        p50 = percentile_from_buckets(vals, counts, 0.50)
        p95 = percentile_from_buckets(vals, counts, 0.95)
        out.append({"date": date, "n": int(n),
                    "p50": round(p50, 1), "p95": round(p95, 1)})
    return out


def _load_json_dict(path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError):
        return {}


def _atomic_write_json(path, obj: dict) -> None:
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class _gen_lock:
    """Эксклюзив на read-merge-write бакетов: writers минимум два (tick_counter
    каждые 30 мин + cron refresh 10:00/20:00, шанс совпадения реальный).
    held=False => вызывающий ОБЯЗАН пропустить тик без записи.
    Без fcntl (Windows-фронт) — no-op с held=True, там docker всё равно None."""
    held = True

    def __enter__(self):
        self.f = None
        try:
            import fcntl
        except ImportError:
            return self
        try:
            GEN_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
            self.f = open(GEN_LOCK_PATH, "w")
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.held = False  # занято: весь тик пропускается (см. _update_gen_days)
        return self

    def __exit__(self, *exc):
        if self.f:
            try:
                self.f.close()
            except OSError:
                pass
        return False


def _parse_iso_utc(s):
    """ISO-строка из state -> aware UTC datetime | None (naive считаем UTC)."""
    if not isinstance(s, str) or not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _docker_logs(since_iso, backfill=False):
    """docker logs penny (опционально --since); None — docker недоступен/ошибка.

    Тот же хелпер-паттерн, что _boot_id: только на хосте, где есть docker;
    на Windows/без прав возвращаем None молча. Имя контейнера уже провалидировано
    на уровне модуля (пустое => не зовём)."""
    if not GEN_CONTAINER:
        return None
    try:
        import shutil
        import subprocess
        if shutil.which("docker") is None:
            return None
        cmd = ["docker", "logs", GEN_CONTAINER]
        if since_iso:
            cmd += ["--since", since_iso]
        r = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace",
            timeout=GEN_BACKFILL_TIMEOUT_S if backfill else GEN_LOG_TIMEOUT_S)
        return r.stdout if r.returncode == 0 else None
        # stderr=STDOUT обязателен: SGLang пишет Decode/Prefill-лог в stderr,
        # docker logs пропускает его в свой stderr — без слияния ряд был вечно пуст
        # (capture_output не совмещать с явными stdout/stderr — ValueError)
    except Exception:
        return None


def _update_gen_days() -> list:
    """Добавить новые строки логов в персистентные бакеты -> server.gen_tps_days.

    Замечания по краям:
    - дедуп ts <= after_ts может съесть строку, пришедшую ровно в секунду шва
      окна (~1 сэмпл/тик, для p50/p95 статистически ничтожно; < давал бы дубли);
    - crash между записью бакетов и state => at-least-once (дубль ~60 с хвоста),
      переносимо;
    - отсутствие файла бакетов при живом state трактуем как «бэкап удалил» —
      сбрасываем backfilled и перечитываем окно заново."""
    with _gen_lock() as lk:
        if not lk.held:  # другой writer внутри критической секции — чистое чтение
            return gen_days_from_buckets(_load_json_dict(GEN_BUCKETS_PATH))
        st = _load_json_dict(GEN_STATE_PATH)
        if st.get("backfilled") and not GEN_BUCKETS_PATH.exists():
            st = {}  # бакеты потеряны — state без них врать не должен
        was_backfilled = bool(st.get("backfilled"))
        buckets = _load_json_dict(GEN_BUCKETS_PATH)
        now = datetime.now(timezone.utc)
        max_ts = _parse_iso_utc(st.get("max_ts_utc"))
        last_run = _parse_iso_utc(st.get("last_run_utc"))
        floor = now - timedelta(hours=GEN_WINDOW_CAP_H)  # окно не растягается простоем
        if st.get("backfilled"):
            cands = [t for t in (max_ts, last_run) if t]
            anchor = min(cands) if cands else now
            since_dt = max(anchor - timedelta(seconds=GEN_OVERLAP_S), floor)
            text = _docker_logs(since_dt.strftime("%Y-%m-%dT%H:%M:%SZ"))
        else:
            # первый заход: ограниченный во времени backfill, не весь лог
            since_dt = now - timedelta(days=GEN_BACKFILL_WINDOW_D)
            text = _docker_logs(since_dt.strftime("%Y-%m-%dT%H:%M:%SZ"), backfill=True)
        if text is not None:
            added, mx = merge_log_lines(buckets, text, after_ts=max_ts)
            if mx is not None and (max_ts is None or mx > max_ts):
                max_ts = mx
            buckets_ok = True
            if added or not was_backfilled:
                # первый успешный заход пишем всегда, даже пустым: иначе свежий
                # контейнер без Decode-строк звал бы 120-с backfill каждый тик
                try:
                    _atomic_write_json(GEN_BUCKETS_PATH, buckets)
                except OSError:
                    buckets_ok = False
            if buckets_ok:  # state вперёд не убежит, если бакеты не дописались
                st = dict(st)
                st["backfilled"] = True  # живой docker видели хотя бы один заход
                st["max_ts_utc"] = max_ts.isoformat() if max_ts else None
                st["last_run_utc"] = now.isoformat(timespec="seconds")
                try:
                    _atomic_write_json(GEN_STATE_PATH, st)
                except OSError:
                    pass
    return gen_days_from_buckets(buckets)


def collect(days: float = 30.0) -> dict:
    """Собрать снимок серверных метрик. days не используется — сигнатура общая."""
    counters: dict = {}
    info: dict = {}
    metrics_ok = info_ok = False

    try:
        counters, kv = _parse_metrics(_fetch("/metrics"))
        metrics_ok = True
    except (OSError, urllib.error.URLError, ValueError, TimeoutError):
        kv = {}

    try:
        info = _parse_server_info(_fetch("/get_server_info"))
        info_ok = True
    except (OSError, urllib.error.URLError, ValueError, TimeoutError):
        info = {}

    online = metrics_ok or info_ok
    if not online:
        counters, info = {}, {}
    elif kv:
        info["kv_pool"] = kv

    # честный серверный cache-hit: 1 - uncached/total (histogram_sum из /metrics)
    if online:
        unc = counters.pop("uncached_prompt_total", None)
        pt = counters.get("prompt_total")
        if unc is not None and pt:
            info["cache_hit_pct"] = round(max(0.0, min(100.0, (1 - unc / pt) * 100)), 1)
            info["uncached_prompt_total"] = int(unc)

    # накопление через рестарты: counters = baseline + текущий бут
    if online and counters:
        try:
            counters, since_boot, base = _fold(counters)
            if base:
                info["counters_since_boot"] = since_boot
                info["counters_baseline"] = base
        except Exception:
            pass  # со state-файлом проблемы — показываем как минимум текущий бут

    # дневной ряд gen throughput: добор docker-логов в персистентные бакеты;
    # нет docker / ошибка — прошлые бакеты, наружу не падаем
    try:
        gen_days = _update_gen_days()
    except Exception:
        gen_days = []

    return {
        "updated_at": _now_iso(),
        "online": online,
        "counters": counters,
        "info": info,
        "gen_tps_days": gen_days,
    }


def _fmt(n: object) -> str:
    return f"{n:,}" if isinstance(n, (int, float)) else "—"


def main() -> int:
    try:  # Windows-консоль может быть не в UTF-8 — печать не должна ронять коллектор
        import sys
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    snap = collect()
    out_dir = HERE / "data"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "server.json"
    out_path.write_text(
        json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    c = snap["counters"]
    i = snap["info"]
    usage = i.get("kv_pool", {}).get("token_usage", i.get("token_usage"))
    usage_s = f"{usage * 100:.1f}%" if isinstance(usage, (int, float)) else "—"
    gd = snap.get("gen_tps_days") or []
    g_last = gd[-1] if gd else None
    gen_s = (f" gen_tps_days={len(gd)} (last {g_last['date']}: p50={g_last['p50']} n={g_last['n']})"
             if g_last else f" gen_tps_days={len(gd)}")
    print(
        f"server online={snap['online']} "
        f"prompt_total={_fmt(c.get('prompt_total'))} "
        f"generation_total={_fmt(c.get('generation_total'))} "
        f"max_total_num_tokens={_fmt(i.get('max_total_num_tokens'))} "
        f"kv_usage={usage_s}{gen_s} -> {out_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
