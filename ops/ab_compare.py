#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A/B харнес: канон NVFP4-bf16mtp vs ABLITERATED-NVFP4.
Модусы: --tag X --out F [--only секция] (merge в существующий отчёт), --compare A B."""
import argparse, json, os, random, re, statistics, subprocess, sys, time, urllib.request

BASE = "http://127.0.0.1:1234"
MODEL = "qwen3.8-flash-next"

def chat(messages, max_tokens=4096, temp=0.0, timeout=900):
    body = {"model": MODEL, "messages": messages, "max_tokens": max_tokens,
            "temperature": temp, "chat_template_kwargs": {"reasoning_effort": "medium"}}
    req = urllib.request.Request(BASE + "/v1/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    j = json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace"))
    dt = time.time() - t0
    m = (j.get("choices") or [{}])[0].get("message", {})
    txt = (m.get("content") or "") + "\n" + (m.get("reasoning_content") or "")
    return txt, j.get("usage") or {}, dt

def code_from(txt):
    blocks = re.findall(r"```[a-zA-Z+#]*\n(.*?)```", txt, re.S)
    return max(blocks, key=len) if blocks else txt

SYSTEM = "Ты — точный кодинг-ассистент. Ответь только одним блоком кода."

def coding_suite():
    results = []
    tasks = [
        ("fib", "Напиши на Python функцию fib(n): fib(0)=0, fib(1)=1, n-е число Фибоначчи, с мемоизацией.",
         lambda ns: ns["fib"](25) == 75025 and ns["fib"](50) == 12586269025 and ns["fib"](0) == 0),
        ("lru", "Напиши на Python класс LRUCache(maxsize) с методами get(key) и put(key,value): O(1), вытеснение по давности, get missing -> -1.",
         lambda ns: (lambda c: (c.put("a",1), c.put("b",2), c.put("c",3),
                     c.get("a")==1 and c.put("d",4) is None and c.get("b")==-1 and c.get("a")==1 and c.get("c")==3 and c.get("d")==4))(ns["LRUCache"](3))),
        ("bugfix", "Исправь баг в функции (ошибается на ')('):\n```python\ndef balance(s):\n    d=0\n    for ch in s:\n        if ch=='(': d+=1\n        if ch==')': d-=1\n    return d==0\n```\nВерни исправленную функцию.",
         lambda ns: ns["balance"]("()") is True and ns["balance"](")(") is False and ns["balance"]("(a(b)c)") is True and ns["balance"]("((())") is False),
        ("topk", "Напиши top_k_words(text,k): k самых частых слов (регистронезависимо), при равной частоте — по алфавиту, вернуть список (word,count).",
         lambda ns: ns["top_k_words"]("dog cat dog bird", 2) == [("dog", 2), ("bird", 1)]),
        ("roman", "Напиши int_to_roman(num) для 1<=num<=3999.",
         lambda ns: ns["int_to_roman"](3999) == "MMMCMXCIX" and ns["int_to_roman"](1994) == "MCMXCIV" and ns["int_to_roman"](58) == "LVIII"),
        ("c-rev", "Напиши программу на C: void rev(char*s) переворачивает строку на месте; main печатает rev('abcdef'). Только код, gcc -O2.", None),
    ]
    for name, prompt, check in tasks:
        rec = {"task": name, "pass": False, "wall": None, "tokens": None, "tps": None, "err": None}
        try:
            txt, usage, dt = chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}])
            code = code_from(txt)
            rec["wall"] = round(dt, 2); rec["tokens"] = usage.get("completion_tokens")
            if rec["tokens"] and dt: rec["tps"] = round(rec["tokens"]/dt, 1)
            if name == "c-rev":
                p = f"/tmp/_ab_{name}.c"; open(p, "w").write(code)
                c = subprocess.run(["gcc", "-O2", "-o", p+".bin", p], capture_output=True, text=True)
                if c.returncode == 0:
                    r = subprocess.run([p+".bin"], capture_output=True, text=True, timeout=10)
                    rec["pass"] = r.stdout.strip() == "fedcba"
                else: rec["err"] = "gcc:" + c.stderr[:120]
            else:
                ns = {}; exec(code, ns); rec["pass"] = bool(check(ns))
        except Exception as e:
            rec["err"] = f"{type(e).__name__}: {str(e)[:160]}"
        results.append(rec)
        print(f"  coding {name}: {'PASS' if rec['pass'] else 'FAIL'} {rec.get('tps')} tok/s {rec.get('err') or ''}", flush=True)
    return results

WORDS = ("kernel tensor gradient pipeline scheduler token latency memory cache device batch "
         "stream queue thread vector matrix layer weight norm attention head expert route "
         "server client request response parser sampler temperature topk nucleus beam").split()
def sentence(rnd):
    return " ".join(rnd.choice(WORDS) for _ in range(rnd.randint(8, 16))).capitalize() + "."

def niahs_suite():
    out = {"single": [], "multi": None, "prompt_tokens": None}
    rnd = random.Random(42)
    total = 25000
    def build(n):
        r = random.Random(42)
        return [sentence(r) for _ in range(n)]
    hay = build(total)
    single_pos = {"101": 0.03, "202": 0.30, "303": 0.60, "404": 0.90}
    multi = {str(500+i): rnd.uniform(0.02+i*0.048, 0.05+i*0.048) for i in range(20)}
    def inject(base_map):
        sents = list(hay)
        for code, frac in base_map.items():
            idx = min(len(sents)-1, max(0, int(len(sents)*min(frac, 0.999))))
            sents[idx] = f"Alarm bell {code} rings loudly over the rack {code}."
        return "\n".join(sents)
    # калибровка: одна пуля с max_tokens=1
    try:
        _, u, _ = chat([{"role": "user", "content": inject({"101": 0.03})}], max_tokens=1, timeout=600)
        pt = u.get("prompt_tokens") or 0
    except Exception as e:
        print(f"  niahs calib FAIL: {e}", flush=True); pt = 0
    if pt > 600000:
        total = max(5000, int(total * 580000 / pt))
        hay = build(total)
        _, u, _ = chat([{"role": "user", "content": inject({"101": 0.03})}], max_tokens=1, timeout=600)
        pt = u.get("prompt_tokens") or 0
    out["prompt_tokens"] = pt
    print(f"  niahs calibrated prompt_tokens={pt} (harness-sent, без system)", flush=True)
    for code, frac in single_pos.items():
        ctx = inject({code: frac})
        try:
            txt, usage, dt = chat([{"role":"system","content":"Отвечай только кодом вида XXX, без пояснений."},
                                   {"role":"user","content": ctx + f"\n\nВопрос: какой код у сигнального звонка Alarm bell {code}? Только код."}],
                                  max_tokens=512)
            ok = code in txt
            out["single"].append({"code": code, "frac": frac, "pass": ok, "resp": txt[:40].strip(),
                                  "prompt_tokens": usage.get("prompt_tokens"), "wall": round(dt,2)})
            print(f"  niahs single {code}@{frac}: {'PASS' if ok else 'FAIL'} pt={usage.get('prompt_tokens')}", flush=True)
        except Exception as e:
            out["single"].append({"code": code, "frac": frac, "pass": False, "err": str(e)[:140]})
            print(f"  niahs single {code}@{frac}: ERR {str(e)[:100]}", flush=True)
    ctxm = inject(multi)
    try:
        txt, usage, dt = chat([{"role":"system","content":"Список кодов, по одному в строке."},
                               {"role":"user","content": ctxm + "\n\nПеречисли ВСЕ коды сигнальных звонков Alarm bell из текста, по одному в строке."}],
                              max_tokens=2048)
        found = sum(1 for c in multi if c in txt)
        out["multi"] = {"found": found, "of": len(multi), "prompt_tokens": usage.get("prompt_tokens"), "wall": round(dt,2)}
        print(f"  niahs multi: {found}/{len(multi)} pt={usage.get('prompt_tokens')}", flush=True)
    except Exception as e:
        out["multi"] = {"found": 0, "of": len(multi), "err": str(e)[:150]}
        print(f"  niahs multi: ERR {str(e)[:100]}", flush=True)
    return out

def speed_suite():
    runs = []
    for i in range(3):
        try:
            txt, usage, dt = chat([{"role":"user","content":"Выпиши числа от 1 до 3000 через запятую, только числа."}], max_tokens=512)
            ct = usage.get("completion_tokens") or 0
            runs.append({"out": ct, "wall": round(dt,2), "tps": round(ct/dt,1) if dt else None})
        except Exception as e:
            runs.append({"err": str(e)[:120]})
    tpss = [r["tps"] for r in runs if r.get("tps")]
    med = statistics.median(tpss) if tpss else None
    print(f"  speed x3: med={med} tok/s", flush=True)
    return {"runs": runs, "median_tps": med}

def codg(d): return sum(1 for r in d["coding"] if r["pass"]), len(d["coding"])
def cmed(d):
    v = [r["tps"] for r in d["coding"] if r.get("tps")]
    return round(statistics.median(v), 1) if v else 0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag"); ap.add_argument("--out"); ap.add_argument("--compare", nargs=2)
    ap.add_argument("--only", choices=["coding", "niahs", "speed"])
    a = ap.parse_args()
    if a.compare:
        x = json.load(open(a.compare[0])); y = json.load(open(a.compare[1]))
        rows = []
        cx, cy = codg(x), codg(y)
        rows.append(("coding pass", f"{cx[0]}/{cx[1]}", f"{cy[0]}/{cy[1]}"))
        rows.append(("coding median tok/s", cmed(x), cmed(y)))
        rows.append(("niahs single (of 4)", sum(1 for r in x["niahs"]["single"] if r.get("pass")),
                     sum(1 for r in y["niahs"]["single"] if r.get("pass"))))
        rows.append(("niahs multi (of 20)", x["niahs"]["multi"].get("found"), y["niahs"]["multi"].get("found")))
        rows.append(("niahs prompt_tokens", x["niahs"].get("prompt_tokens"), y["niahs"].get("prompt_tokens")))
        rows.append(("speed median tok/s", x["speed"]["median_tps"] or 0, y["speed"]["median_tps"] or 0))
        print(f"{'metric':28s} {x['tag']:16s} {y['tag']:16s}")
        for m, va, vb in rows: print(f"{str(m):28s} {str(va):16s} {str(vb):16s}")
        return
    rep = None
    if a.out and os.path.exists(a.out):
        try: rep = json.load(open(a.out))
        except Exception: rep = None
    if rep is None or rep.get("tag") != a.tag:
        rep = {"tag": a.tag, "ts": time.strftime("%F %T"), "coding": [], "niahs": None, "speed": None}
    if a.only in (None, "coding"): rep["coding"] = coding_suite()
    if a.only in (None, "niahs"):  rep["niahs"] = niahs_suite()
    if a.only in (None, "speed"):  rep["speed"] = speed_suite()
    json.dump(rep, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("saved", a.out, flush=True)

if __name__ == "__main__":
    main()
