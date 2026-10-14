#!/usr/bin/env python3
"""Коллектор метрик инференс-сервера (SGLang) для токенов-дашборда.

Контракт: CONTRACT.md п.2 (источник "Сервер инфера"), ключ схемы "server":
    { updated_at: iso, online: bool,
      counters: { prompt_total, generation_total },   # int, сумма ВСЕХ серий
      info: { max_total_num_tokens, ... } }           # что реально удалось вытянуть

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
from datetime import datetime, timezone
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

    return {
        "updated_at": _now_iso(),
        "online": online,
        "counters": counters,
        "info": info,
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
    print(
        f"server online={snap['online']} "
        f"prompt_total={_fmt(c.get('prompt_total'))} "
        f"generation_total={_fmt(c.get('generation_total'))} "
        f"max_total_num_tokens={_fmt(i.get('max_total_num_tokens'))} "
        f"kv_usage={usage_s} -> {out_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
