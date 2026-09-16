#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Benchmark one serving configuration: quality + decode throughput + acceptance.

Throughput and acceptance come from the engine's own decode log lines, not from
wall-clock over HTTP -- see docs/MEASUREMENTS.md for why that distinction matters.

Environment:
  BENCH_URL      chat completions endpoint (default http://127.0.0.1:1234/v1/chat/completions)
  BENCH_MODEL    served model name        (default qwen3.8-flash-next)
  BENCH_LOGSRC   'journal:<unit>' or 'docker:<container>'  (default journal:sglang)
  BENCH_TEMP     sampling temperature     (default 0)
  BENCH_TOPP     top_p                    (default 1.0)
  BENCH_TOPK     top_k                    (default -1)
  BENCH_PROMPT   override the generation prompt

Usage:
  python3 bench.py "label for this configuration"
"""
import json
import os
import re
import statistics as st
import subprocess
import sys
import time
import urllib.request

URL = os.environ.get("BENCH_URL", "http://127.0.0.1:1234/v1/chat/completions")
MODEL = os.environ.get("BENCH_MODEL", "qwen3.8-flash-next")
LOGSRC = os.environ.get("BENCH_LOGSRC", "journal:sglang")
TEMP = float(os.environ.get("BENCH_TEMP", "0"))
TOPP = float(os.environ.get("BENCH_TOPP", "1.0"))
TOPK = int(os.environ.get("BENCH_TOPK", "-1"))

PROMPT = os.environ.get("BENCH_PROMPT") or (
    "Explain Dijkstra's algorithm in detail and provide a complete Python "
    "implementation with a priority queue, including complexity analysis."
)

# Tasks with a single verifiable answer. Catches a broken configuration; it will
# not rank two working ones -- everything healthy scores 20/20.
TASKS = [
    ("What is 17 * 23? Reply with only the number.", "391"),
    ("What is 2^12? Reply with only the number.", "4096"),
    ("What is 1000 - 387? Reply with only the number.", "613"),
    ("How many letters are in the word 'strawberry'? Reply with only the number.", "10"),
    ("How many times does the letter 'r' appear in 'strawberry'? Reply with only the number.", "3"),
    ("What is the output of: print(len([1,2,3] + [4,5])) ? Reply with only the number.", "5"),
    ("What is the output of: print(sorted([3,1,2])[0]) ? Reply with only the number.", "1"),
    ("What is the output of: print('abc'[::-1]) ? Reply with only the result.", "cba"),
    ("What is the output of: print(10 // 3) ? Reply with only the number.", "3"),
    ("What is the output of: print(bool([])) ? Reply with only True or False.", "False"),
    ("In Python, what does list.pop() return by default: first or last element? One word.", "last"),
    ("What is the time complexity of binary search? Reply with only the big-O notation.", "O(log n)"),
    ("What HTTP status code means 'Not Found'? Reply with only the number.", "404"),
    ("What is the output of: print(3 == 3.0) ? Reply with only True or False.", "True"),
    ("What is 15% of 200? Reply with only the number.", "30"),
    ("What is the output of: print(len('hello world'.split())) ? Reply with only the number.", "2"),
    ("In git, which command creates a new branch and switches to it in one step? Reply with only the command.", "checkout -b"),
    ("What is the output of: print(sum(range(5))) ? Reply with only the number.", "10"),
    ("What is the default port for HTTPS? Reply with only the number.", "443"),
    ("What is the output of: print(type(5/2).__name__) ? Reply with only the type name.", "float"),
]


def ask(prompt, max_tokens=40):
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": TEMP,
        "top_p": TOPP,
        "top_k": TOPK,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(
        URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    data = json.loads(urllib.request.urlopen(req, timeout=300).read())
    msg = data["choices"][0]["message"]
    # With a reasoning parser the answer may live in reasoning_content.
    text = (msg.get("reasoning_content") or "") + (msg.get("content") or "")
    return text.strip(), data["usage"]["completion_tokens"]


def normalise(s):
    return re.sub(r"\s+", " ", s.strip().strip(".").strip()).lower()


def quality():
    passed, failures = 0, []
    for question, expected in TASKS:
        try:
            got, _ = ask(question)
        except Exception as exc:                       # noqa: BLE001
            got = "<error {}>".format(type(exc).__name__)
        g, w = normalise(got), normalise(expected)
        if g == w or w in g:
            passed += 1
        else:
            failures.append((question[:45], expected, got[:40]))
    return passed, len(TASKS), failures


def read_log(since):
    kind, _, target = LOGSRC.partition(":")
    if kind == "docker":
        cmd = ["docker", "logs", "--since", since.replace(" ", "T") + "Z", target]
    else:
        cmd = ["journalctl", "-u", target or "sglang", "--since", since, "--no-pager", "-o", "cat"]
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    return proc.stdout + proc.stderr


def decode_stats(since):
    throughput, accept = [], []
    for line in read_log(since).splitlines():
        if "Decode batch" not in line:
            continue
        running = re.search(r"#running-req:\s*(\d+)", line)
        if not running or running.group(1) != "1":
            continue
        t = re.search(r"gen throughput \(token/s\):\s*([0-9.]+)", line)
        a = re.search(r"accept len:\s*([0-9.]+)", line)
        if t and float(t.group(1)) > 20:
            throughput.append(float(t.group(1)))
        if a:
            accept.append(float(a.group(1)))
    return throughput, accept


def percentile(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * q))]


def speed(runs=4, max_tokens=1000):
    ask("warmup", 8)
    since = time.strftime("%Y-%m-%d %H:%M:%S")
    time.sleep(1)
    for _ in range(runs):
        ask(PROMPT, max_tokens)
    time.sleep(2)
    return decode_stats(since)


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "unnamed"
    print("### {}".format(label))
    print("    sampling: temp={} top_p={} top_k={}".format(TEMP, TOPP, TOPK))

    passed, total, failures = quality()
    print("QUALITY:  {}/{} = {:.0f}%".format(passed, total, 100.0 * passed / total))
    for question, expected, got in failures:
        print("   miss: {!r} expected {!r} got {!r}".format(question, expected, got))

    throughput, accept = speed()
    if throughput:
        print("SPEED:    n={} median {:.1f}  p90 {:.1f}  max {:.1f}".format(
            len(throughput), st.median(throughput),
            percentile(throughput, 0.9), max(throughput)))
    else:
        print("SPEED:    no decode lines found -- check BENCH_LOGSRC")
    if accept:
        print("ACCEPT:   median {:.2f}  p90 {:.2f}  max {:.2f}".format(
            st.median(accept), percentile(accept, 0.9), max(accept)))


if __name__ == "__main__":
    main()
