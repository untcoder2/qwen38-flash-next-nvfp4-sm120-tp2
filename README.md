# Qwen3.8-Flash-Next NVFP4 on 2× RTX PRO 6000 Blackwell (SM120)

Measured notes from a two-card SM120 box. Three things here are not documented
anywhere else that we could find:

1. **The official NVIDIA NVFP4 checkpoint ships an fp8 MTP block that silently
   destroys speculative decoding** — draft acceptance 1.50 instead of ~3.0, costing
   ~40% of decode throughput. Output quality is unaffected; only speed is.
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

Context 786,432 (YaRN factor 3.0), KV pool 2,254,464 tokens, fp8_e4m3 KV,
8 concurrent requests, PLE table resident in VRAM.

Sampling matters: the same build measures 308.3 at temperature 0 and 274.7 at
production sampling. Compare like with like.

## Environment

2× RTX PRO 6000 Blackwell Workstation, 96 GB each, SM120, **no NVLink** (PCIe,
topology SYS — IOMMU must be off for P2P). Driver 595.84, CUDA 13.0,
torch 2.13.0+cu130, FlashInfer 0.6.17, Python 3.12.13, 90 GB host RAM.

## Findings

- **[The fp8 MTP defect](docs/MTP.md)** — what is wrong with the official
  checkpoint's draft head, how to confirm it in one command, and the transplant
  that fixes it. Reproduced independently in two different runtimes.
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

## What we did not verify

Stated plainly so nobody builds on sand:

- **Multi-agent throughput** (partly closed). First concurrent A/B on v2.5.1:
  8 agent-style lanes give 1,228 tok/s aggregate (median 156 tok/s per lane)
  vs 886 (148/lane) at 6 lanes — our TP=2 admission 8 already dominates the
  upstream C6-style six-request profile. A proper load report is still pending.
- **Long-uptime accept stability** (improved evidence). Production runs
  `--mamba-track-interval 256`; hourly accept medians held 2.3-2.5 across a
  40 h uptime window (~24k decode samples/hour in busy hours, retract count 0).
  The 64-interval decay claim remains untested on our path.
- **Degeneration in long reasoning.** There is a credible report of unrecoverable
  repetition loops tied to the draft-verify path under heavy tool use and long
  reasoning. We have a probe but no verdict yet.
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
