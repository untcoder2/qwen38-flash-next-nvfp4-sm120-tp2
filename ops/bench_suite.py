#!/usr/bin/env python3
"""ops/bench_suite.py — единый воспроизводимый стенд скорости/TTFT/акцепта.

Один запуск = фиксированный голден-сет (ops/bench_prompts.json) + три бакета
нагрузки, метрики на запрос (TTFT по SSE, клиентский tps, серверный tps,
spec_accept_length), агрегаты p50/p95, JSON-отчёт в ~/agent-tools/reports и
print-таблица. Два отчёта сравнивает ops/bench_compare.py — это A/B конфигов
(W8 vs W4, v3.0.1 vs v3.1.1, ...) на ОДНОМ И ТОМ ЖЕ контенте.

Перед КАЖДЫМ сэмплом стенд ждёт тишины (/get_load num_reqs==0), иначе сэмпл
пропускается — иначе собственный фоновый трафик делает A/B ложным.

Запускать НА ХОСТЕ:  python3 -I ~/agent-tools/bench_suite.py --label v311-w8
Ключи: --sets short,med,long | --reps N (short) | --meta "строка контекста"
       | --url https://127.0.0.1:1234 | --ca /home/molochko/penny/tls/ca.pem
       | --idle-timeout 60
Только stdlib. К проду — только HTTP, ничего не меняет.
"""
import argparse, hashlib, json, os, ssl, statistics, sys, time, urllib.request

DEFAULTS = dict(
    url="https://127.0.0.1:1234",
    ca=os.path.expanduser("~/penny/tls/ca.pem"),
    prompts=os.path.join(os.path.dirname(os.path.abspath(__file__)), "bench_prompts.json"),
    out_dir=os.path.expanduser("~/agent-tools/reports"),
)
SAMPLING = {"temperature": 1.0, "top_p": 0.95, "top_k": 20}  # официальные дефолты Qwen-карты
GREEDY = {"temperature": 0}


def pct(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    idx = min(len(s) - 1, max(0, int(round(q / 100 * (len(s) - 1)))))
    return s[idx]


class Runner:
    def __init__(self, url, ca, idle_timeout):
        self.url = url.rstrip("/")
        self.ctx = ssl.create_default_context(cafile=ca)
        self.idle_timeout = idle_timeout
        self.idle_waits = 0
        self.skipped = 0

    def _req(self, path, body=None, stream=False):
        req = urllib.request.Request(self.url + path,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        return urllib.request.urlopen(req, timeout=300, context=self.ctx)

    def wait_idle(self):
        """Тишина до --idle-timeout сек. True = инфер пуст, можно мерить."""
        deadline = time.time() + self.idle_timeout
        waited = False
        while time.time() < deadline:
            try:
                load = json.loads(self._req("/get_load").read())
                entries = load if isinstance(load, list) else [load]
                busy = any((d.get("num_reqs", 0) or 0) > 0 or (d.get("num_waiting_reqs", 0) or 0) > 0
                           for d in entries)
            except Exception:
                busy = False
            if not busy:
                if waited:
                    self.idle_waits += 1
                return True
            waited = True
            time.sleep(2)
        self.skipped += 1
        return False

    def nonstream(self, text, mnt, sampling):
        if not self.wait_idle():
            return None
        t0 = time.perf_counter()
        r = self._req("/generate", {"text": text,
                                    "sampling_params": dict(sampling, max_new_tokens=mnt),
                                    "stream": False})
        wall = time.perf_counter() - t0
        m = json.loads(r.read())["meta_info"]
        ctok = m.get("completion_tokens", 0)
        acc = m.get("spec_accept_length")  # None, если спекуляции нет — не фабрикуем 0.0
        e2e = m.get("e2e_latency")
        return dict(t_client=ctok / wall if wall > 0 else None,
                    t_server=(ctok / e2e) if e2e else None,
                    accept=acc, out_tok=ctok, wall=wall)

    def ttft_stream(self, text, mnt, sampling):
        if not self.wait_idle():
            return None
        t0 = time.perf_counter()
        r = self._req("/generate", {"text": text,
                                    "sampling_params": dict(sampling, max_new_tokens=min(mnt, 64)),
                                    "stream": True})
        first = None
        for line in r:
            if line.startswith(b"data:"):
                first = time.perf_counter() - t0
                break
        # стрим дочитываем до конца: обрыв до EOS сервер мучит ошибками в /logs,
        # а следующий замер должен стартовать с чистым соединением
        try:
            while r.readline():
                pass
        except Exception:
            pass
        return first


def run_set(runner, items, reps, do_ttft, greedy):
    samples, ttfts = [], []
    per_kind = {}
    for it in items:
        for _ in range(reps):
            smp = GREEDY if greedy else SAMPLING
            res = runner.nonstream(it["text"], it["max_new_tokens"], smp)
            if res is None:
                continue
            samples.append(res)
            if it.get("kind"):
                per_kind.setdefault(it["kind"], []).append(res)
            if do_ttft:
                f = runner.ttft_stream(it["text"], it["max_new_tokens"], smp)
                if f is not None:
                    ttfts.append(f)
    t = [x["t_client"] for x in samples if x["t_client"]]
    ts = [x["t_server"] for x in samples if x["t_server"]]
    acc = [x["accept"] for x in samples if x["accept"] is not None]
    out = {
        "n": len(samples),
        "t_client_p50": pct(t, 50), "t_client_p95": pct(t, 95),
        "t_server_p50": pct(ts, 50),
        "accept_p50": pct(acc, 50), "accept_p95": pct(acc, 95),
        "ttft_n": len(ttfts),
        "ttft_p50": pct(ttfts, 50), "ttft_p95": pct(ttfts, 95),
        "out_tok_total": sum(x["out_tok"] for x in samples),
    }
    if per_kind:
        out["per_kind"] = {k: {"t_client": round(statistics.median([x["t_client"] for x in v]), 1),
                               "accept": round(statistics.median([x["accept"] for x in v]), 2)}
                           for k, v in per_kind.items()}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--meta", default="")
    ap.add_argument("--sets", default="short,med,long")
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--idle-timeout", type=int, default=60)
    ap.add_argument("--url", default=DEFAULTS["url"])
    ap.add_argument("--ca", default=DEFAULTS["ca"])
    ap.add_argument("--prompts", default=DEFAULTS["prompts"])
    ap.add_argument("--out-dir", default=DEFAULTS["out_dir"])
    a = ap.parse_args()

    gold = json.load(open(a.prompts))
    runner = Runner(a.url, a.ca, a.idle_timeout)
    runner._req("/health").read()  # health-gate: прод должен отвечать

    sets = a.sets.split(",")
    report = {"label": a.label, "meta": a.meta,
              "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "bench_sha": hashlib.sha256(open(__file__, "rb").read()).hexdigest()[:16],
              "prompts_version": gold.get("version"),
              "sampling": "temp1.0/top_p0.95/top_k20 (long: greedy)",
              "idle_waits": None, "skipped": None, "sets": {}}
    if "short" in sets:
        report["sets"]["short"] = run_set(runner, gold["short"], a.reps, True, False)
    if "med" in sets:
        report["sets"]["med"] = run_set(runner, gold["med"], 1, False, False)
    if "long" in sets:
        report["sets"]["long"] = run_set(runner, gold["long"], 1, False, True)
    report["idle_waits"] = runner.idle_waits
    report["skipped"] = runner.skipped
    report["agent_score_tps_p50"] = report["sets"].get("short", {}).get("t_client_p50")

    os.makedirs(a.out_dir, exist_ok=True)
    path = os.path.join(a.out_dir,
                        time.strftime("%Y%m%d-%H%M%S") + "-" + a.label.replace(" ", "_") +
                        "-%s.json" % report["bench_sha"][:8])
    with open(path, "w") as f:
        json.dump(report, f, indent=2)
    print("report:", path, "| idle_waits=%d skipped=%d" % (runner.idle_waits, runner.skipped))
    print("| label | set | n | tps c-p50 | tps c-p95 | tps s-p50 | accept p50 | TTFT p50 |")
    print("|---|---|---|---|---|---|---|---|")
    for s, r in report["sets"].items():
        fmt = lambda v: ("%.2f" % v) if isinstance(v, (int, float)) else "—"
        print("| %s | %s | %d | %s | %s | %s | %s | %s |" % (
            a.label, s, r["n"], fmt(r["t_client_p50"]), fmt(r["t_client_p95"]),
            fmt(r["t_server_p50"]), fmt(r["accept_p50"]), fmt(r["ttft_p50"])))
    if runner.skipped:
        print("ВНИМАНИЕ: %d сэмплов пропущено из-за не-тишины — абсолюты могут быть смещены" % runner.skipped)


if __name__ == "__main__":
    main()
