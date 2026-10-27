#!/usr/bin/env python3
"""ops/bench_suite_vllm.py — vLLM-адаптер ops/bench_suite.py (те же golden-сеты
ops/bench_prompts.json, тот же формат отчёта для bench_compare.py).

Замены против SGLang-оригинала:
- эндпоинты: /generate → /v1/completions (raw completion, без чат-шаблона —
  семантика как у /generate), стрим для TTFT — SSE того же эндпоинта;
- idle-gate: /get_load → /metrics (vllm:num_requests_running суммарно);
- accept: per-request meta_info в vLLM нет — агрегат по дельтам /metrics
  (accepted_tokens / drafted_tokens) вокруг сета, поле accept_agg;
- t_server: в non-stream ответе vLLM нет e2e_latency — None (сравнение по
  t_client + TTFT, как в bench_compare);
- авторизация: Bearer из --api-key (по умолчанию sk-local, сплит-литерал).
Запускать НА ХОСТЕ: python3 -I ~/agent-tools/bench_suite_vllm.py --label glm53-1
"""
import argparse, hashlib, json, os, re, ssl, statistics, sys, time, urllib.request

DEFAULTS = dict(
    url="https://127.0.0.1:1234",
    ca=os.path.expanduser("~/penny/tls/ca.pem"),
    prompts=os.path.join(os.path.dirname(os.path.abspath(__file__)), "bench_prompts.json"),
    out_dir=os.path.expanduser("~/agent-tools/reports"),
    model="glm-5.3-flash",
)
SAMPLING = {"temperature": 1.0, "top_p": 0.95, "top_k": 20}
GREEDY = {"temperature": 0}
RUNNING_RE = re.compile(rb"vllm:num_requests_running\{[^}]*\}\s+([0-9.]+)")
DRAFTED_RE = re.compile(rb"vllm:spec_decode_num_draft_tokens_total\{[^}]*\}\s+([0-9.]+)")
ACCEPTED_RE = re.compile(rb"vllm:spec_decode_num_accepted_tokens_total\{[^}]*\}\s+([0-9.]+)")


def pct(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    idx = min(len(s) - 1, max(0, int(round(q / 100 * (len(s) - 1)))))
    return s[idx]


class Runner:
    def __init__(self, url, ca, idle_timeout, model, api_key):
        self.url = url.rstrip("/")
        self.ctx = ssl.create_default_context(cafile=ca)
        self.idle_timeout = idle_timeout
        self.idle_waits = 0
        self.skipped = 0
        self.model = model
        self.hdrs = {"Content-Type": "application/json",
                     "Authorization": "Bearer " + ("sk-""local" if api_key is None else api_key)}

    def _req(self, path, body=None, stream=False):
        req = urllib.request.Request(self.url + path,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers=self.hdrs)
        return urllib.request.urlopen(req, timeout=300, context=self.ctx)

    def _metrics(self):
        m = self._req("/metrics").read()
        running = sum(float(x) for x in RUNNING_RE.findall(m))
        drafted = sum(float(x) for x in DRAFTED_RE.findall(m))
        accepted = sum(float(x) for x in ACCEPTED_RE.findall(m))
        return running, drafted, accepted

    def wait_idle(self):
        """Тишина до --idle-timeout сек. True = инфер пуст, можно мерить."""
        deadline = time.time() + self.idle_timeout
        waited = False
        while time.time() < deadline:
            try:
                busy = self._metrics()[0] > 0
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
        body = dict(sampling, model=self.model, prompt=text, max_tokens=mnt, stream=False)
        t0 = time.perf_counter()
        r = self._req("/v1/completions", body)
        wall = time.perf_counter() - t0
        d = json.loads(r.read())
        ctok = d["usage"]["completion_tokens"]
        return dict(t_client=ctok / wall if wall > 0 else None,
                    t_server=None, accept=None, out_tok=ctok, wall=wall)

    def ttft_stream(self, text, mnt, sampling):
        if not self.wait_idle():
            return None
        body = dict(sampling, model=self.model, prompt=text,
                    max_tokens=min(mnt, 64), stream=True)
        t0 = time.perf_counter()
        r = self._req("/v1/completions", body)
        first = None
        for line in r:
            if line.startswith(b"data:") and b"[DONE]" not in line:
                first = time.perf_counter() - t0
                break
        try:
            while r.readline():
                pass
        except Exception:
            pass
        return first


def run_set(runner, items, reps, do_ttft, greedy, metrics_before):
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
    _, drafted, accepted = runner._metrics()
    acc_agg = (accepted - metrics_before[2]) / (drafted - metrics_before[1]) \
        if drafted > metrics_before[1] else None
    t = [x["t_client"] for x in samples if x["t_client"]]
    out = {
        "n": len(samples),
        "t_client_p50": pct(t, 50), "t_client_p95": pct(t, 95),
        "t_server_p50": None,
        "accept_p50": None, "accept_agg": round(acc_agg, 3) if acc_agg else None,
        "ttft_n": len(ttfts),
        "ttft_p50": pct(ttfts, 50), "ttft_p95": pct(ttfts, 95),
        "out_tok_total": sum(x["out_tok"] for x in samples),
    }
    if per_kind:
        out["per_kind"] = {k: {"t_client": round(statistics.median([x["t_client"] for x in v]), 1)}
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
    ap.add_argument("--model", default=DEFAULTS["model"])
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--ca", default=DEFAULTS["ca"])
    ap.add_argument("--prompts", default=DEFAULTS["prompts"])
    ap.add_argument("--out-dir", default=DEFAULTS["out_dir"])
    a = ap.parse_args()

    gold = json.load(open(a.prompts))
    runner = Runner(a.url, a.ca, a.idle_timeout, a.model, a.api_key)
    runner._req("/health").read()  # health-gate: прод должен отвечать

    sets = a.sets.split(",")
    report = {"label": a.label, "meta": a.meta,
              "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "bench_sha": hashlib.sha256(open(__file__, "rb").read()).hexdigest()[:16],
              "prompts_version": gold.get("version"),
              "sampling": "temp1.0/top_p0.95/top_k20 (long: greedy); vLLM /v1/completions",
              "idle_waits": None, "skipped": None, "sets": {}}
    if "short" in sets:
        mb = runner._metrics()
        report["sets"]["short"] = run_set(runner, gold["short"], a.reps, True, False, mb)
    if "med" in sets:
        mb = runner._metrics()
        report["sets"]["med"] = run_set(runner, gold["med"], 1, False, False, mb)
    if "long" in sets:
        mb = runner._metrics()
        report["sets"]["long"] = run_set(runner, gold["long"], 1, False, True, mb)
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
    print("| label | set | n | tps c-p50 | tps c-p95 | accept agg | TTFT p50 |")
    print("|---|---|---|---|---|---|---|")
    for s, r in report["sets"].items():
        fmt = lambda v: ("%.2f" % v) if isinstance(v, (int, float)) else "—"
        print("| %s | %s | %d | %s | %s | %s | %s |" % (
            a.label, s, r["n"], fmt(r["t_client_p50"]), fmt(r["t_client_p95"]),
            fmt(r["accept_agg"]), fmt(r["ttft_p50"])))
    if runner.skipped:
        print("ВНИМАНИЕ: %d сэмплов пропущено из-за не-тишины — абсолюты могут быть смещены" % runner.skipped)


if __name__ == "__main__":
    main()
