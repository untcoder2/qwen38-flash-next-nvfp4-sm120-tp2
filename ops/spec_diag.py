#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""spec_diag.py — диагностика спекулятивного декодирования на живом стенде.

Только read-only операции с инфером: GET /get_server_info, GET /metrics и
обычные POST /generate (greedy). Никаких рестартов, флагов, set_internal_state.

Источник истины — meta_info каждого ответа (не гауги /metrics, там EMA).
Legacy-алиас spec_accept_histogram сознательно НЕ читается — берём
spec_correct_drafts_histogram.

Режимы:
  --mode smoke   1 зонд, печатает полный набор ключей meta_info
  --mode bs1     12 промптов последовательно
  --mode bs4     12 промптов пачками по 4 (xargs-подобный пул)
  --mode bs8     12 промптов пачками по 8
  --mode long    3 промпта с max_new_tokens=2000 (стрим, окна по приёмистости)
  --mode report  сводная таблица по JSONL

Мягкость: общий бюджет зондов (--budget, по умолчанию 40) учитывается по
файлу-счётчику, поэтому пережить его двумя запусками нельзя. Перед каждой
пачкой проверяется живой #running-req из логов penny: пачка не запускается,
если текущая загрузка + размер пачки превысят --max-total-running.
"""
import argparse, json, os, random, ssl, statistics as st, subprocess, sys, time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = os.environ.get("SPEC_DIAG_BASE", "https://127.0.0.1:1234")
_CTX = ssl.create_default_context(cafile=os.environ.get("SPEC_DIAG_CA", "/home/molochko/penny/tls/ca.pem"))
CONTAINER = os.environ.get("SPEC_DIAG_CONTAINER", "penny")
# форк Pennyroyal отбивает seed в sampling_params в TypeError на валидации
# аргументов — greedy t=0 и так детерминирован, поэтому seed по умолчанию не шлём.
USE_SEED = False

# whitelisted поля meta_info из форка; legacy aliасы исключены
SPEC_KEYS = [
    "spec_accept_length", "spec_accept_rate",
    "spec_num_correct_drafts", "spec_num_proposed_drafts",
    "spec_verify_ct", "spec_cap_length", "spec_block_accept_length",
    "spec_correct_drafts_histogram",
]
BANNED_KEYS = ["spec_accept_histogram"]

# 12 коротких детерминированных промптов (один класс задачи — код/инструкционная
# формулировка, как в агентном трафике). Один и тот же набор во всех фазах.
PROMPTS = [
    "Напиши на Python функцию is_palindrome(s): игнорировать регистр и не-буквы, вернуть bool.",
    "Напиши на Python класс Stack с push/pop/peek/is_empty на списке, O(1) операции, только код.",
    "Дана строка s. Напиши count_vowels(s) на Python без регулярных выражений. Только код.",
    "Напиши на Python функцию flatten(lst) для произвольной вложенности списков. Только код.",
    "Отредактируй: 'Он сказал что опаздывает на десять минут потому что был трафик.' Верни чистый вариант и одно пояснение.",
    "Напиши на Python функцию second_largest(nums) без сортировки, O(n), вернуть None если элементов меньше двух.",
    "Перечисли 5 шагов загрузки веб-страницы от DNS до рендера. Кратко, нумерованным списком.",
    "Напиши на Python функцию parse_kv(text) для строк вида key=value, верни dict, пропускай мусорные строки.",
    "Объясни в 4 предложениях чем отличается стек от очереди. Никакого кода.",
    "Напиши на Python decorator retry(times, delay) для синхронных функций с экспоненциальным ожиданием.",
    "Дан список слов. Напиши longest_common_prefix(words) на Python, вернуть '' если список пуст.",
    "Напиши на Python функцию sanitize_filename(name): оставить буквы, цифры, дефисы и подчёркивания.",
]

# 3 промпта на длинную генерацию (~1500-2000 токенов)
LONG_PROMPTS = [
    "Напиши подробный рассказ про рыцаря, который идёт через неоновое подземелье: "
    "описания стен, диалоги с хранителем, магические ловушки, финальная битва. "
    "Пиши связно и максимально длинно, не менее 1500 слов, без списков.",
    "Составь максимально подробный технический план самописного биллинга LLM-инференса: "
    "учёт токенов, спекулятивное декодирование, батчинг, метрики, отчётность. "
    "Пиши сплошным связным текстом, развернуто, не менее 1500 слов.",
    "Напиши подробный урок для студента: как устроено спекулятивное декодирование, "
    "драфт-модель, верификация, accept length, почему батч влияет на скорость. "
    "Связным текстом, не менее 1500 слов, без формул.",
]


def getj(path, timeout=20):
    req = urllib.request.Request(BASE + path)
    return json.loads(urllib.request.urlopen(req, timeout=timeout, context=_CTX).read().decode("utf-8", "replace"))


def server_spec():
    try:
        d = getj("/get_server_info", timeout=20)
        return {k: v for k, v in d.items() if "spec" in k.lower() or k in
                ("sglang_version", "max_total_num_tokens", "context_length")}
    except Exception as e:
        return {"ERROR": repr(e)}


def metrics_snapshot():
    """Гауги спекуляций из /metrics — справочно (EMA), не источник истины."""
    want = ("sglang:spec_accept_length", "sglang:spec_accept_rate", "sglang:spec_cap_length",
            "sglang:spec_block_accept_length", "sglang:spec_num_steps",
            "sglang:spec_num_draft_tokens", "sglang:num_running_reqs", "sglang:gen_throughput")
    try:
        txt = urllib.request.urlopen(BASE + "/metrics", timeout=20, context=_CTX).read().decode("utf-8", "replace")
    except Exception as e:
        return {"ERROR": repr(e)}
    out = {}
    for line in txt.splitlines():
        if line.startswith("#"):
            continue
        key = line.split("{", 1)[0].split(" ", 1)[0]
        if key in want:
            try:
                out[key] = float(line.rsplit(" ", 1)[1])
            except Exception:
                pass
    return out


def live_running(max_age_s=40):
    """Максимум #running-req по свежим строкам лога scheduler'а (0 если логов нет)."""
    try:
        r = subprocess.run(["docker", "logs", "--since", f"{max_age_s}s", CONTAINER],
                           capture_output=True, text=True, timeout=30)
        txt = (r.stdout or "") + (r.stderr or "")
    except Exception:
        return None
    vals = []
    for line in txt.splitlines():
        i = line.find("#running-req:")
        if i >= 0:
            try:
                vals.append(int(line[i + len("#running-req:"):].split()[0]))
            except Exception:
                pass
    return max(vals) if vals else 0


def pick_spec(meta):
    rec = {}
    for k in SPEC_KEYS:
        if k in meta:
            v = meta[k]
            rec[k] = v
    for k in BANNED_KEYS:
        if k in meta and k not in rec:
            rec[k + "__legacy_ignored"] = True
    return rec


def _post_generate(body, timeout):
    req = urllib.request.Request(BASE + "/generate", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    raw = urllib.request.urlopen(req, timeout=timeout, context=_CTX).read().decode("utf-8", "replace")
    wall = time.time() - t0
    return wall, json.loads(raw)


def probe(prompt_id, prompt, max_new_tokens, seed, mode, bs):
    body = {"text": prompt,
            "sampling_params": {"temperature": 0, "max_new_tokens": max_new_tokens,
                                **({"seed": seed} if USE_SEED else {})}}
    rec = {"ts": round(time.time(), 3), "mode": mode, "bs": bs, "prompt_id": prompt_id,
           "max_new_tokens": max_new_tokens, "seed": seed}
    try:
        wall, j = _post_generate(body, timeout=300 if max_new_tokens <= 512 else 900)
        meta = j.get("meta_info") or {}
        rec.update(ok=True, wall_s=round(wall, 2),
                   completion_tokens=meta.get("completion_tokens") or meta.get("output_tokens"),
                   finish=meta.get("finish_reason"), spec=pick_spec(meta),
                   meta_keys=sorted(meta.keys()))
    except Exception as e:
        rec.update(ok=False, err=repr(e)[:300], spec={})
    return rec


def probe_stream_windowed(prompt_id, prompt, max_new_tokens, seed, mode, bs, max_samples=60):
    """Стрим /generate: снимаем кумулятивные spec-счётчики по вехам генерации.

    Даёт accept length по окнам (старт/середина/конец) не трогая step-логи.
    """
    body = {"text": prompt,
            "sampling_params": {"temperature": 0, "max_new_tokens": max_new_tokens,
                                **({"seed": seed} if USE_SEED else {})},
            "stream": True}
    req = urllib.request.Request(BASE + "/generate", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    rec = {"ts": round(time.time(), 3), "mode": mode, "bs": bs, "prompt_id": prompt_id,
           "max_new_tokens": max_new_tokens, "seed": seed, "stream": True}
    samples, final_meta, nchunks, t0 = [], {}, 0, time.time()
    try:
        with urllib.request.urlopen(req, timeout=900, context=_CTX) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    chunk = json.loads(line[5:])
                except Exception:
                    continue
                nchunks += 1
                meta = chunk.get("meta_info") or {}
                if meta:
                    final_meta = meta
                got = meta.get("completion_tokens") or meta.get("output_tokens") or 0
                if not samples or (got - samples[-1]["completion_tokens"]) >= max(1, max_new_tokens // max_samples):
                    samples.append({"completion_tokens": got, "t_rel": round(time.time() - t0, 2),
                                    **{k: meta.get(k) for k in SPEC_KEYS if k in meta}})
                if meta.get("finish_reason"):
                    break
        wall = time.time() - t0
        rec.update(ok=True, wall_s=round(wall, 2), chunks=nchunks,
                   completion_tokens=final_meta.get("completion_tokens") or final_meta.get("output_tokens"),
                   finish=final_meta.get("finish_reason"), spec=pick_spec(final_meta),
                   meta_keys=sorted(final_meta.keys()), window_samples=samples)
    except Exception as e:
        rec.update(ok=False, err=repr(e)[:300], spec={}, window_samples=samples)
    return rec


def counter_path(out_file):
    """Файл-счётчик израсходованного бюджета зондов (живёт рядом с JSONL)."""
    return out_file + ".budget"


def budget_used(out_file):
    try:
        with open(counter_path(out_file)) as f:
            return json.load(f).get("used", 0)
    except Exception:
        return 0


def budget_add(out_file, n):
    used = budget_used(out_file) + n
    try:
        with open(counter_path(out_file), "w") as f:
            json.dump({"used": used, "ts": time.time()}, f)
    except Exception:
        pass
    return used


def phase(mode, bs, prompts, max_new_tokens, seed, out_file, budget,
          gap_min=15.0, gap_max=30.0, max_total_running=8, wait_deadline_s=300):
    used = budget_used(out_file)
    todo = len(prompts)
    if used + todo > budget:
        print(f"[ABORT] бюджет: уже {used} из {budget}, фаза требует {todo}. Меньше фаз или явный --budget.", file=sys.stderr)
        return 2

    def gate(size):
        """Ждём пока live_running + size <= max_total_running; False если не дождались."""
        t0 = time.time()
        while True:
            cur = live_running()
            if cur is None:
                print(f"[gate] running-req недоступен по логам — считаю безопасным", file=sys.stderr)
                return True
            if cur + size <= max_total_running:
                return True
            if time.time() - t0 > wait_deadline_s:
                print(f"[gate] timeout: running-req={cur}, пачка {size} не проходит (лимит total {max_total_running})", file=sys.stderr)
                return False
            print(f"[gate] running-req={cur} -> жду 20s (хочу пачку {size})", file=sys.stderr)
            time.sleep(20)

    def one(i_p):
        i, p = i_p
        if mode == "long":
            return probe_stream_windowed(i, p, max_new_tokens, seed + i, mode, bs)
        return probe(i, p, max_new_tokens, seed + i, mode, bs)

    records = []
    batches = [prompts[k:k + bs] for k in range(0, len(prompts), bs)]
    for bi, batch in enumerate(batches):
        if not gate(bs):
            print(f"[ABORT] capacity gate не пропустил пачку #{bi}", file=sys.stderr)
            break
        items = batch
        with ThreadPoolExecutor(max_workers=bs) as ex:
            recs = list(ex.map(one, items))
        records.extend(recs)
        for r in recs:
            s = r.get("spec") or {}
            print(json.dumps({"pid": r["prompt_id"], "ok": r.get("ok"), "wall": r.get("wall_s"),
                              "tok": r.get("completion_tokens"),
                              "acc": s.get("spec_accept_length"),
                              "corr": s.get("spec_num_correct_drafts"),
                              "prop": s.get("spec_num_proposed_drafts"),
                              "vct": s.get("spec_verify_ct"),
                              "cap": s.get("spec_cap_length"),
                              "blk": s.get("spec_block_accept_length"),
                              "hist": s.get("spec_correct_drafts_histogram"),
                              "err": r.get("err")}, ensure_ascii=False))
        with open(out_file, "a") as f:
            for r in records[bi * bs:]:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        budget_add(out_file, len(recs))
        if mode == "bs1":
            time.sleep(random.uniform(3.0, 6.0))
        elif bi < len(batches) - 1:
            gap = random.uniform(gap_min, gap_max)
            print(f"[sleep] {gap:.0f}s между пачками", file=sys.stderr)
            time.sleep(gap)
    print(f"[done] mode={mode} bs={bs} записано {len(records)} в {out_file} (бюджет {budget_used(out_file)}/{budget})", file=sys.stderr)
    return 0


def wmean(vals):
    return None if not vals else sum(vals) / len(vals)


def pct(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    i = (len(s) - 1) * q
    lo, hi = int(i), min(int(i) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


def report(out_file):
    rows = []
    with open(out_file) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    groups = {}
    for r in rows:
        groups.setdefault((r.get("mode"), r.get("bs")), []).append(r)
    hdr = f"{'mode':6s} {'bs':>3s} {'n':>3s} {'acc_med':>8s} {'p25':>6s} {'p75':>6s} {'corr/prop':>10s} {'cap':>12s} {'block_acc':>12s} {'wall_med':>9s} {'tok_med':>8s}"
    print(hdr)
    print("-" * len(hdr))
    for (mode, bs), rs in sorted(groups.items(), key=lambda kv: (str(kv[0][0]), kv[0][1] or 0)):
        ok = [r for r in rs if r.get("ok")]
        acc = [r["spec"].get("spec_accept_length") for r in ok if isinstance(r["spec"].get("spec_accept_length"), (int, float))]
        corr = sum(r["spec"].get("spec_num_correct_drafts") or 0 for r in ok)
        prop = sum(r["spec"].get("spec_num_proposed_drafts") or 0 for r in ok)
        caps = [r["spec"].get("spec_cap_length") for r in ok if isinstance(r["spec"].get("spec_cap_length"), (int, float))]
        blks = [r["spec"].get("spec_block_accept_length") for r in ok if isinstance(r["spec"].get("spec_block_accept_length"), (int, float))]
        walls = [r["wall_s"] for r in ok if isinstance(r.get("wall_s"), (int, float))]
        toks = [r["completion_tokens"] for r in ok if isinstance(r.get("completion_tokens"), (int, float))]
        fmt = lambda v, n=3: ("—" if v is None else f"{v:.{n}f}")
        print(f"{str(mode):6s} {str(bs):>3s} {len(ok):>3d} {fmt(st.median(acc) if acc else None):>8s}"
              f" {fmt(pct(acc,.25) if acc else None,2):>6s} {fmt(pct(acc,.75) if acc else None,2):>6s}"
              f" {(f'{corr/prop:.3f}' if prop else '—'):>10s}"
              f" {('mean '+f'{wmean(caps):.2f}'+'/max '+f'{max(caps):.0f}') if caps else '—':>12s}"
              f" {('mean '+f'{wmean(blks):.2f}'+'/max '+f'{max(blks):.0f}') if blks else '—':>12s}"
              f" {fmt(st.median(walls) if walls else None,1):>9s} {fmt(st.median(toks) if toks else None,0):>8s}")
    # гистограмма correct_drafts популяциями
    print("\nГистограмма correct_drafts (сколько принято из proposed, по mode/bs):")
    for (mode, bs), rs in sorted(groups.items(), key=lambda kv: (str(kv[0][0]), kv[0][1] or 0)):
        agg = {}
        for r in rs:
            h = (r.get("spec") or {}).get("spec_correct_drafts_histogram")
            if isinstance(h, dict):
                for k, v in h.items():
                    agg[int(k)] = agg.get(int(k), 0) + int(v)
            elif isinstance(h, list):
                for i, v in enumerate(h):
                    agg[i] = agg.get(i, 0) + int(v)
        if agg:
            tot = sum(agg.values())
            print(f"  {mode}/bs{bs}: " + " ".join(f"{k}:{agg[k]}({agg[k]/tot*100:.0f}%)" for k in sorted(agg)) + f" | всего батчей {tot}")
        else:
            print(f"  {mode}/bs{bs}: гистограммы нет в meta_info")
    # окна для long: в этой сборке форка spec-счётчики пишутся в meta_info
    # только на финальном бэче (_calculate_spec_decoding_metrics под `if state.finished`,
    # tokenizer_manager.py:2494), поэтому посрединный accept из стрима не достаётся —
    # вместо него разрез accept по длине генерации по всей выборке
    longs = [r for r in rows if r.get("mode") == "long" and r.get("ok")]
    if longs:
        print("\naccept по длине генерации (все режимы; окна стрима недоступны — см. комментарий в коде):")
        buckets = [("<150", 0, 150), ("150-250", 150, 250), ("250-330", 250, 330),
                   ("330-1000", 330, 1000), ("1000+", 1000, 10**9)]
        pairs = [(r["completion_tokens"], r["spec"]["spec_accept_length"])
                 for r in rows if r.get("ok")
                 and isinstance(r.get("completion_tokens"), (int, float))
                 and isinstance((r.get("spec") or {}).get("spec_accept_length"), (int, float))]
        for name, lo, hi in buckets:
            v = [a for t, a in pairs if lo <= t < hi]
            if v:
                print(f"  {name:>9s} tok: n={len(v):>3d} acc med={st.median(v):.3f} "
                      f"[{min(v):.2f}..{max(v):.2f}]")
        if len(pairs) > 3:
            xs = [t for t, _ in pairs]
            ys = [a for _, a in pairs]
            mx, my = wmean(xs), wmean(ys)
            num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
            den = (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
            print(f"  корреляция Пирсона длина↔accept по всей выборке (n={len(pairs)}): "
                  f"{num/den:+.3f}" if den else "  корреляция: н/д")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["smoke", "bs1", "bs4", "bs8", "long", "report"])
    ap.add_argument("--out", default=os.path.expanduser("~/agent-tools/spec_diag.jsonl"))
    ap.add_argument("--budget", type=int, default=40)
    ap.add_argument("--max-new-tokens", type=int, default=None)
    ap.add_argument("--seed", type=int, default=20261010)
    ap.add_argument("--gap-min", type=float, default=15.0)
    ap.add_argument("--gap-max", type=float, default=30.0)
    ap.add_argument("--max-total-running", type=int, default=8)
    ap.add_argument("--wait-deadline", type=int, default=300)
    ap.add_argument("--bs", type=int, default=None, help="перебить размер пачки")
    args = ap.parse_args()

    print(f"[ctx] container={CONTAINER} base={BASE}", file=sys.stderr)
    print(f"[ctx] metrics(EMA, справочно): {json.dumps(metrics_snapshot(), ensure_ascii=False)}", file=sys.stderr)

    if args.mode == "report":
        return report(args.out)

    if args.mode == "smoke":
        srv = server_spec()
        print(f"[ctx] server_info spec: {json.dumps({k: v for k, v in srv.items() if 'adapt' in k or k in ('speculative_algorithm','speculative_num_steps','speculative_eagle_topk','speculative_num_draft_tokens','speculative_accept_threshold_single','speculative_accept_threshold_acc')}, ensure_ascii=False)}", file=sys.stderr)
        r = probe(0, PROMPTS[0], min(256, args.max_new_tokens or 256), args.seed, "smoke", 1)
        with open(args.out, "a") as f:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        budget_add(args.out, 1)
        print(json.dumps(r, ensure_ascii=False, indent=1)[:4000])
        missing = [k for k in SPEC_KEYS if k not in (r.get("spec") or {})]
        print(f"\n[check] отсутствуют в meta_info: {missing if missing else 'нет — все 8 ключей на месте'}", file=sys.stderr)
        return 0

    bs_map = {"bs1": 1, "bs4": 4, "bs8": 8, "long": 1}
    bs = args.bs or bs_map[args.mode]
    prompts = ([(i, p) for i, p in enumerate(LONG_PROMPTS)] if args.mode == "long"
               else [(i, p) for i, p in enumerate(PROMPTS)])
    mnt = args.max_new_tokens or (2000 if args.mode == "long" else 320)
    if args.mode != "long" and not (256 <= mnt <= 400):
        print(f"[ABORT] max_new_tokens={mnt} вне разрешённого коридора 256..400", file=sys.stderr)
        return 2
    return phase(args.mode, bs, prompts, mnt, args.seed, args.out, args.budget,
                 args.gap_min, args.gap_max, args.max_total_running, args.wait_deadline)


if __name__ == "__main__":
    sys.exit(main())
