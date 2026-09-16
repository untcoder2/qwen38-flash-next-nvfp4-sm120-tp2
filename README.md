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

## What we did not verify

Stated plainly so nobody builds on sand:

- **Multi-agent throughput.** Every number here is a single request. The prior
  two-card setup did 422–451 tok/s aggregate on four agents; the new one is
  unmeasured under concurrency.
- **Long-uptime accept stability.** The runtime we settled on uses
  `--mamba-track-interval 64`, which a community report names as the trigger for
  draft acceptance decaying toward zero over ~24 h of uptime. Our previous setup
  used 256 and stayed flat over a 27-hour window. Unverified on the new one.
- **Degeneration in long reasoning.** There is a credible report of unrecoverable
  repetition loops tied to the draft-verify path under heavy tool use and long
  reasoning. We have a probe but no verdict yet.
- **HiCache persistence across restart** is disabled here (NIXL backend would not
  initialize).
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
