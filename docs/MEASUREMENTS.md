# Measurements: every number, and how it was taken

## Method

- **Throughput** is the engine's own `gen throughput (token/s)` from its decode log
  lines, filtered to `#running-req: 1`, values above 20 tok/s (below that is batch
  tail). Not wall-clock over HTTP — see "mistakes" below.
- **Acceptance** is `accept len` from the same lines.
- **Quality** is 20 tasks with single verifiable answers (arithmetic, Python
  semantics, protocol facts), scored by exact or substring match.
- Every run warms up first, then four 1000-token generations.
- Statistics computed with `LC_ALL=C` or in Python.

Hardware: 2x RTX PRO 6000 Blackwell Workstation 96 GB, SM120, **no NVLink**
(PCIe, topology SYS, IOMMU off). Driver 595.84, CUDA 13.0, torch 2.13.0+cu130,
FlashInfer 0.6.17, Python 3.12.13, 90 GB host RAM.

## Full table

Quality was 20/20 in every configuration below. Unless noted, temperature 0.

| # | configuration | cards | median | p90 | max | accept |
|---|---|---|---|---|---|---|
| A | stock fork, BF16 MTP transplant | 2 | 214.8 | 227.8 | 241.9 | 2.51 |
| B | A, speculation K=2 (steps 2, draft 3) | 2 | 204.7 | 225.3 | 231.2 | 2.23 |
| C | official checkpoint, own fp8 MTP | 2 | 124.8 | 142.1 | 159.1 | 1.50 |
| D | C + patch keyed on `FP8_PB_WO` | 2 | 126.3 | 147.9 | 153.3 | 1.48 |
| D2 | C + patch as published (branch fires) | 2 | *crash* | | | |
| D3 | D2 with DeepGEMM runner | 2 | *crash at graph capture* | | | |
| E | A, no replayssm-spec, bf16 SSM, flashinfer decode | 2 | 206.7 | 230.8 | 245.6 | 2.50 |
| F | A + loader release-refs patch | 2 | 206.2 | 233.5 | 244.0 | 2.48 |
| P | runtime-2, official checkpoint | 1 | 97.2 | 110.1 | 132.3 | 1.50 |
| P2 | runtime-2, BF16 transplant, online MXFP8 | 1 | 259.1 | 298.9 | 319.4 | 3.06 |
| P3 | P2 + FR-Spec | 1 | 192.2 | 256.1 | 328.3 | 2.17 |
| T | runtime-3 (turbo), BF16 transplant | 1 | 141.6 | 169.1 | 204.1 | 1.64 |
| T2 | T, `gdn-mtp-cache-mode full` | 1 | 139.3 | 185.2 | 266.9 | 1.62 |
| **TP2** | **runtime-2, TP=2, PLE in VRAM** | **2** | **308.3** | **339.1** | **384.7** | **3.01** |
| TP2s | TP2 at production sampling (t=1.0/0.95/20) | 2 | 274.7 | 313.4 | 353.3 | 2.91 |

Notes on individual rows:

- **B** — a community report found K=2 beat K=3 on wall-clock. It did not
  reproduce here; shrinking the draft also lowers the acceptance ceiling.
- **D** — the patch was keyed on the string the config file contains
  (`FP8_PB_WO`). At runtime the algorithm resolves as `FP8_BLOCK_SCALES`, so the
  branch never fired. See [MTP.md](MTP.md).
- **E** — worth knowing: the flag that forces float32 SSM state, which we had
  suspected of costing throughput, costs nothing measurable either way.
- **F** — a patch that frees 8.2 GB of stale tensors during load elsewhere did
  nothing here: identical 81 GiB unit memory peak and identical
  `available_gpu_mem`. Our PLE table is resident in VRAM, so there is nothing to
  free.
- **T/T2** — turbo's published numbers are on a different checkpoint. On the
  transplant checkpoint its draft degrades and neither the SSM dtype nor the GDN
  cache mode explains it.

## Long-run context

Fourteen days of production decode lines, single-request only, by day:

| date | n | median | p90 | max | accept median / max |
|---|---|---|---|---|---|
| day 1 | 1,498 | 67.2 | 92.7 | 182.8 | 2.83 / 4.00 |
| day 4 | 9,835 | 156.8 | 218.1 | 376.5 | 2.08 / 3.35 |
| day 7 | 73,423 | 151.2 | 205.5 | 431.9 | 1.93 / 4.00 |
| day 10 | 14,004 | 118.7 | 194.6 | 415.6 | 1.62 / 4.00 |
| day 14 | 11,935 | 162.8 | 216.8 | 408.2 | 2.15 / 4.00 |

Two lessons. First, day-wide medians (150-170) and controlled benchmark medians
(210-310) are different populations; real traffic contains far more hard-to-predict
content than any benchmark prompt. Do not compare across them. Second, the dips are
days of heavy experimentation, not regressions — they recover the next day.

## Mistakes we made, so you do not repeat them

**Wall-clock over HTTP is not throughput.** `curl` timing of a 1000-token
completion gave 215 tok/s where the engine reported 308 for the same work. The
difference is prefill plus HTTP. Published figures for this model are decode-only;
compare against decode-only.

**`sort -n` truncates decimals in a comma-decimal locale.** Our first statistics
pass reported a p90 of 264 that did not exist. Use `LC_ALL=C` or compute in Python.

**Temperature changes speculative acceptance.** At temperature 0 acceptance is
"does the draft match the argmax". Under real sampling it is probabilistic and
lower: 3.01 to 2.91, and 308.3 to 274.7 tok/s on the same build. Measure at your
production sampling settings, not at greedy.

**With a reasoning parser, the answer is not in `content`.** Our first
degeneration probe scored 8/8 clean because the model had not finished thinking at
the token limit, so `content` was empty and `reasoning_content` held everything.
Concatenate both.

**Do not compare a one-card result to a two-card deployment.** We spent hours
benchmarking single-card configurations that could never have been deployed at our
context length and KV pool, before trying the two-card path that turned out to be
both possible and faster.
