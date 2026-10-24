#!/usr/bin/env python3
"""ops/bench_compare.py — дифф двух отчётов ops/bench_suite.py (A/B конфигов).

Использование: python3 bench_compare.py A.json B.json
A — базовый (куда смотрим «от»), B — сравниваемый. Печатает markdown-таблицу
дельт по каждому сету и каждой метрике (tps/accept/TTFT) с процентом и
стрелкой; агент-скор — отдельной строкой. Выход 0 всегда (это отчёт, не гейт).
"""
import json, sys

METRICS = [("t_client_p50", "ток/с клиент p50", False),
           ("t_server_p50", "ток/с сервер p50", False),
           ("accept_p50", "акцепт p50", False),
           ("accept_p95", "акцепт p95", False),
           ("ttft_p50", "TTFT p50, c", True),
           ("ttft_p95", "TTFT p95, c", True)]


def d(a, b, lower_better=False):
    if a is None or b is None or a == 0:
        return "—"
    p = (b - a) / a * 100
    good = (p < 0) if lower_better else (p > 0)
    arrow = "✅" if abs(p) < 1 else ("🔼" if good else "🔽")
    return "%+.1f%% %s" % (p, arrow)


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    A, B = (json.load(open(p)) for p in sys.argv[1:3])
    if A.get("prompts_version") != B.get("prompts_version"):
        sys.exit("STOP: prompts_version %s != %s — набор менялся, A/B некорректен"
                 % (A.get("prompts_version"), B.get("prompts_version")))
    if A.get("bench_sha") and B.get("bench_sha") and A["bench_sha"] != B["bench_sha"]:
        print("WARNING: версия стенда различается (%s vs %s) — сравнение только индикативное\n"
              % (A["bench_sha"][:8], B["bench_sha"][:8]))
    print("# Сравнение: **%s** (база) → **%s**\n" % (A["label"], B["label"]))
    print("| набор | метрика | A | B | дельта |")
    print("|---|---|---|---|---|")
    for s in ("short", "med", "long"):
        ra, rb = A["sets"].get(s, {}), B["sets"].get(s, {})
        if not ra or not rb:
            continue
        print("| %s | **n** | %d | %d | %s |" % (s, ra.get("n", 0), rb.get("n", 0),
              "равно" if ra.get("n") == rb.get("n") else "РАЗЛИЧАЕТСЯ — осторожно"))
        for k, name, lower in METRICS:
            if k in ra and k in rb and ra[k] is not None and rb[k] is not None:
                print("| %s | %s | %.2f | %.2f | %s |" % (s, name, ra[k], rb[k], d(ra[k], rb[k], lower)))
        for kind, va in sorted(ra.get("per_kind", {}).items()):
            vb = rb.get("per_kind", {}).get(kind)
            if vb:
                print("| long·%s | ток/с | %.1f | %.1f | %s |" % (kind, va["t_client"], vb["t_client"], d(va["t_client"], vb["t_client"])))
                print("| long·%s | акцепт | %.2f | %.2f | %s |" % (kind, va["accept"], vb["accept"], d(va["accept"], vb["accept"])))
    sa, sb = A.get("agent_score_tps_p50"), B.get("agent_score_tps_p50")
    if sa and sb:
        print("\n**Агент-скор (short tps c-p50): %.1f → %.1f  (%s)**" % (sa, sb, d(sa, sb)))
    print("\n_промпт-сет v%s; семплинг у B: %s_" % (B.get("prompts_version"), B.get("sampling")))
    print("_метаданные B: %s_" % (B.get("meta") or "—"))


if __name__ == "__main__":
    main()
