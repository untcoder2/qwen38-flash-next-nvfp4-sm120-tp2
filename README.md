# Qwen3.8-Flash-Next NVFP4 on 2× RTX PRO 6000 Blackwell (SM120)

Measured notes from a two-card SM120 box. Three things here are not documented
anywhere else that we could find:

1. **The official NVIDIA NVFP4 checkpoint ships an fp8 MTP block that silently
   destroys speculative decoding** — draft acceptance 1.50 instead of ~3.0, costing
   ~40% of decode throughput. Output quality is unaffected; only speed is.
   *Update 19.10:* NVIDIA's QAD checkpoint (`FP4-Local`) now ships a trained NVFP4
   draft head; BF16-materialized into the tree it takes acceptance to **4.51** —
   see [MTP.md](docs/MTP.md).
2. **The single-GPU-targeted SM120 runtimes do run at TP=2**, despite being
   documented as TP=1 only. One flag and two removals are all it takes.
3. **You can pass GPUs into Docker without `nvidia-container-toolkit` and without
   root**, which unblocks these container builds on locked-down hosts.

Everything below is measured on one machine. Numbers are decode throughput as
reported by the engine itself (`gen throughput`), single request, after warmup.
Quality is a 20-task set with verifiable answers, run for every configuration.

## Results

All configurations scored **20/20 on quality**. Differences are speed only.

| configuration | cards | tok/s median | p90 | accept len |
|---|---|---|---|---|
| Official NVFP4, its own fp8 MTP | 2 | 124.8 | 142.1 | **1.50** |
| Official NVFP4 + community patch for fp8 MTP | 2 | *crashes* | — | — |
| Official weights + BF16 MTP transplant, stock fork | 2 | 214.8 | 227.8 | 2.51 |
| **+ Pennyroyal v2.5 runtime, online MXFP8, TP=2** | 2 | **308.3** | **339.1** | **3.01** |
| same, production sampling (t=1.0, top_p 0.95, top_k 20) | 2 | 274.7 | 313.4 | 2.91 |
| **+ Pennyroyal v2.5.1, cleaned quant map, no draft-quant flag** | 2 | **332.2** | 339 (n=5) | 2.4-2.55; 4.00 deterministic probe |
| **+ Pennyroyal v2.5.2, after 48 h multi-agent soak** | 2 | 313 (greedy median) | — | 2.4–2.6; retract **0** |
| **+ Pennyroyal v2.5.3 @ context 1,048,576 (YaRN×4), mf 0.92** | 2 | 326–332 (greedy) | — | 2.46–2.63; retract 0 |
| same engine @ 786,432 (YaRN×3), mf 0.95, 16 running reqs | 2 | 264 (greedy, **under 5–7 agent load**) | — | 2.25–2.6; retract 0 |
| **+ PLE to host-pinned RAM, v3.0.1, hicache 10 (KV ×2.6)** | 2 | **405.6** (greedy, n=5, quiet) | — | retract 0 |
| **+ NVIDIA QAD MTP head (FP4-Local dequant to BF16), v3.0.1** | 2 | **412.5** | — (n=5: 406.5–470.9) | **4.51** (greedy window, n=21) |
| **+ same head on Pennyroyal v3.0.2** (adaptive width-hold) | 2 | **380.8** quiet (bimodal 319–447, −8% vs baseline) | — (n=5: 349.9–427.5) | 2.00 in adversarial window; degeneration probe improved to 0/8; production rolled back to v3.0.1 — see upstream issue #81 (v3.0.2 added `switch_grace_batches` hold to the BS1 policy; we read same-second 7→3→7 reversals as a hold violation — maintainer later noted the hold counts *completed verify-feedback batches* (~0.8 s at bench rate), so timestamps alone don't prove a skip; either way the throughput loss was real and the mechanism was replaced: v3.1 fixed-width policy closed #81) |
| **+ same head on Pennyroyal v3.1.1** — production: **fixed-W4 policy** (ops; after A/B below) | 2 | agent-score **199** t/s (golden short p50, temp 1.0); old list-style bench reads 357 (−13%, known prompt-shape artifact) | — | real-content accept p50 2.4–3.1 at any width; A/B golden set W8→W4: short +11%, med +18%, long p50 +31% (prose +21%, code +31%); W8 only wins on rote enumeration (+29%); zero width-switch events in either; battery 20/20, degen 1/8, KV pool 6,425,920 |
| **+ same, PLE back in VRAM: mf 0.93, chunk 8192, HiCache 30 (26.10)** | 2 | list-style bench **414.6** median (n=5: 376–435); golden short p50 184 t/s — parity with the warmed RAM-era series | — | KV pool **2,746,496**; retract 0, kernel OOM-kill 0 through soak + full bench; 169K cold prefill 12.39 s; refill cold→warm 3.6→0.2 s; host tier allocated 3.36M tokens, 0 tokens served so far; mount-table lesson and numbers in [MEASUREMENTS.md](docs/MEASUREMENTS.md) and [PLE-RAM.md](docs/PLE-RAM.md) |

Context history matters when comparing: the early rows ran 786,432 (YaRN factor 3.0),
we stretched to 1,048,576 (YaRN×4) with v2.5.3, and moved back to 786,432/×3 — the extra
stretch bought nothing on our workload (mid-decode stalls near the length cap at ×4; see
the [v2.5.2–v2.5.3 field report](docs/FIELD-REPORT.md) and issue #9 follow-up). KV pool
2,254,464 → 2,445,440 (mf 0.92) → 2,752,640 tokens (mf 0.95), fp8_e4m3 KV, PLE table
resident in VRAM — then **6,425,920 on v3.0.1 after moving PLE to host-pinned RAM**
([PLE-RAM.md](docs/PLE-RAM.md)). Concurrency: 8 requests / 48 mamba slots early on; production now
16 / 128 (head + agent fleet — see [TP2.md §5](docs/TP2.md)).

Sampling matters: the same build measures 308.3 at temperature 0 and 274.7 at
production sampling. And load matters: the 264 above is single-stream greedy while
five to seven agents share the box; the same build measures 326–333 on a quiet engine.
Compare like with like.

## Environment

2× RTX PRO 6000 Blackwell Workstation, 96 GB each, SM120, **no NVLink** (PCIe,
topology SYS — IOMMU must be off for P2P). Driver 595.84, CUDA 13.0,
torch 2.13.0+cu130, FlashInfer 0.6.17, Python 3.12.13, 90 GB host RAM.

## Findings

- **[The fp8 MTP defect](docs/MTP.md)** — what is wrong with the official
  checkpoint's draft head, how to confirm it in one command, the transplant
  that fixed it, and the 19.10 update: NVIDIA's QAD head supersedes the
  transplant (accept 4.51). Reproduced independently in two different runtimes.
- **[Running TP=2 on single-GPU builds](docs/TP2.md)** — the exact changes, with
  the errors you hit if you skip them.
- **[GPUs in Docker without nvidia-container-toolkit](docs/GPU-IN-DOCKER.md)** —
  device passthrough plus four bind-mounted driver libraries.
- **[FR-Spec reduced vocabulary is language-bound](docs/FRSPEC.md)** — measured
  Russian vs English; a mismatched map costs ~25% and cannot be fixed by rebuilding
  it for your language *and* beating full vocabulary.
- **[Measurement methodology](docs/MEASUREMENTS.md)** — every number above, how it
  was taken, and the mistakes we made taking them.
- **[Artifact hygiene: fix the checkpoint, not the flags](docs/ARTIFACT-HYGIENE.md)** —
  the stale `mtp.*` entry in `hf_quant_config.json`, removing it, and the draft then
  loading correctly with **no** `--speculative-draft-model-quantization unquant`
  workaround; plus byte-identity (31/31 sha256) between our BF16 MTP transplant and
  the community re-shard by @dicksondickson.
- **[Rollback contract for a live engine](docs/ROLLBACK.md)** ([tools/](tools/)) —
  digest-pinned snapshots with /proc truth, six read-only verify gates, one-command
  atomic rollback. Every engine change since is executed as snapshot -> edit ->
  restart through the supervisor -> verify.
- **[Field report: v2.5.2 → v2.5.3 under production](docs/FIELD-REPORT.md)** — the
  48 h soak, the six-gate regression runs, the YaRN×4 → YaRN×3 / 1M → 768K move and
  why we went back, the mf-0.95 KV growth, and three upstream notes (mf↔hicache,
  NIXL namespace field drift, agentic concurrency).
- **[PLE placement, revisited](docs/PLE-RAM.md)** — host-pinned RAM beats VRAM on
  the v3.0.1 engine: KV pool ×2.6 to 6,425,920, no measurable prefill/decode cost,
  and a correction of TP2.md §2, which published the opposite claim for the older
  runtime.

## What we did not verify

Stated plainly so nobody builds on sand:

- **Multi-agent throughput** (largely closed). First concurrent A/B on v2.5.1:
  8 agent-style lanes give 1,228 tok/s aggregate (median 156 tok/s per lane)
  vs 886 (148/lane) at 6 lanes. Continuous production since then runs the whole
  fleet against the head 24/7; over 10 days: ~1.65B unique tokens through ~140k
  model steps, server-side prefix-cache hit ≈ 98.7 % (from
  `uncached_prompt_tokens_histogram` vs `prompt_tokens_total`), retract 0
  throughout the soaks. The admission path, not the cache, was our bottleneck —
  see the concurrency note in [TP2.md §5](docs/TP2.md).
- **Long-uptime accept stability** (improved evidence). Production runs
  `--mamba-track-interval 256`; hourly accept medians held 2.3-2.5 across a
  40 h uptime window (~24k decode samples/hour in busy hours, retract count 0).
  The 64-interval decay claim remains untested on our path.
- **Degeneration in long reasoning.** There is a credible report of unrecoverable
  repetition loops tied to the draft-verify path under heavy tool use and long
  reasoning. First verdict (19.10, QAD head, adversarial probe temp 1.0): 1
  repetition burst in 8 at both 8K and 32K budgets, 6/8 ended with a natural
  stop, zero empty completions. No baseline exists for the older community head —
  the probe was never run before the swap.
- **HiCache persistence across restart** is disabled here (NIXL backend would not
  initialize). v2.5.1's host-cache fixes check out indirectly — a warm 169,608-token
  re-prefill dropped from 13.0 s to 0.42 s (100.0% radix hits) — but
  eviction-forced host restores are still unproven on this box.
- **Custom all-reduce is off**, so on a no-NVLink box there is still latency left
  on the table.

## Credit

The runtimes measured here are other people's work:
[jpezzulli/sglang-rtxpro6000](https://github.com/jpezzulli/sglang-rtxpro6000)
(Pennyroyal) and
[mratsim/sglang-qwen38fn-sm120-turbo](https://github.com/mratsim/sglang-qwen38fn-sm120-turbo).
This repository contributes measurements and the TP=2 path, not a new engine.

## License

MIT — see [LICENSE](LICENSE).
