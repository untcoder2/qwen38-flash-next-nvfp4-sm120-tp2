# Черновики постов (не для публичного репозитория)

Четыре текста под четыре площадки. Ссылки и порядок — в конце файла.

---

## 1. NVIDIA — новая тема

**Куда:** https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4/discussions → New discussion

**Заголовок:**

```
NEXTN draft acceptance collapses to 1.5: mtp.* fp8 experts are built unquantized
```

**Текст:**

---

Serving this checkpoint with NEXTN speculative decoding gives a draft acceptance
length of **1.50** where the same hardware and engine reach **~3.0** with a BF16
MTP block. That is roughly 40% of decode throughput, lost silently — no error, no
warning, no NaN.

Measured on 2x RTX PRO 6000 Blackwell (SM120, no NVLink), SGLang, driver 595.84,
CUDA 13.0, torch 2.13.0+cu130, FlashInfer 0.6.17:

| checkpoint | accept len (median) | tok/s (median) |
|---|---|---|
| this one, as published | 1.50 | 124.8 |
| same weights, BF16 MTP block transplanted in | 2.51 | 214.8 |

Reproduced in a second, unrelated SM120 runtime on the same box: 1.50 as published,
3.06 with the transplant. So this looks like a property of the artifact rather than
of one engine.

### What is in the files

```
tensors total .............. 299,545
mtp.* tensors ..............   3,101
weight_scale_inv tensors ...   1,536   (all of them inside mtp.*)
mtp.* dtypes ............... 1,565 BF16 + 1,536 F8_E4M3
```

`config.json`:

```json
"quantized_layers": {
  "mtp.layers.0.mlp.experts": {"quant_algo": "FP8_PB_WO", "group_size": 128}
}
```

So the draft MoE experts are fp8 with 128x128 blockwise `weight_scale_inv` scales.

### Why acceptance collapses

`ModelOptMixedPrecisionConfig.get_quant_method()` has a linear branch for
`FP8_PB_WO`, but its MoE branch only handles `FP8`, `MXFP8` and `NVFP4`. The draft
experts fall through and are constructed **unquantized**, then receive fp8 weights
without their scales. The drafter emits noise, the target model rejects nearly
every proposal, acceptance lands just above 1.

Note for anyone reproducing: the config file says `FP8_PB_WO`, and reading it
offline confirms that, but at runtime the same lookup resolves to
`FP8_BLOCK_SCALES`. The name is normalised somewhere in the load path. We wasted
several hours on a fix keyed to the wrong string before instrumenting the running
process:

```
prefix='mtp.layers.0.mlp.experts' algo='FP8_BLOCK_SCALES' layer=FusedMoE
```

### The community patch does not work on SM120

There is a patch circulating that adds the missing MoE branch and pins the Triton
MoE runner. It does fire on this checkpoint, and then the server dies on the first
real batch:

```
RuntimeError: Triton Error [CUDA]: an illegal memory access was encountered
  at scheduler.run_batch
```

Isolated: single launcher, no supervisor races, model fully loaded, readiness
logged, crash on the first request. Switching the pinned runner to DeepGEMM — the
natural choice for 128x128 blockwise fp8 — fails earlier, at CUDA graph capture:

```
CUDA driver error 719 (CUDA_ERROR_LAUNCH_FAILED)
  in m_grouped_fp8_gemm_nt_contiguous
```

On this build the only MoE runner that works at all is `flashinfer_cutlass`, and
`Fp8MoEMethod` has no flashinfer_cutlass path. So as far as we can tell there is
currently no working MoE runner for these fp8 MTP experts on SM120.

### Output quality is not affected

Worth stating so nobody panics. With exact verification the target validates every
drafted token against its full vocabulary, so a broken drafter costs speed and
nothing else. We scored 20/20 on a verifiable-answer set in every configuration,
including the one at acceptance 1.50.

### Request

Either ship the MTP block in BF16, as the community re-releases do, or publish a
working MoE path for `FP8_PB_WO` / `FP8_BLOCK_SCALES` on SM120. As published, every
user who enables NEXTN on this checkpoint loses roughly 40% of decode throughput
and has no signal that anything is wrong.

Full write-up, measurement methodology and the benchmark harness:
https://github.com/untcoder2/qwen38-flash-next-nvfp4-sm120-tp2

---

## 2. Qwen #47 — комментарий в существующий тред

**Куда:** https://huggingface.co/Qwen/Qwen3.8-Flash-Next/discussions/47

**Текст:**

---

Adding a second data point for 2x RTX PRO 6000 (SM120, no NVLink, TP=2), since
this thread is the only published two-card measurement we could find.

Two things that moved the number for us:

**1. The draft head.** The official NVIDIA NVFP4 checkpoint's own fp8 MTP block
collapses acceptance to 1.50 (its MoE experts are built unquantized). Transplanting
a BF16 MTP block takes acceptance to ~3.0 and throughput from 124.8 to 214.8 tok/s.

**2. TP=2 on the single-GPU SM120 runtimes.** They are documented as TP=1 only, but
they do run on two cards — you need `--disable-custom-all-reduce`, otherwise
startup dies with `RuntimeError: invalid argument` in `get_graph_buffer_ipc_meta`.
With the PLE table kept resident in VRAM instead of offloaded (which is what the
second card buys you) and online MXFP8 enabled:

| | tok/s median | p90 | accept | KV pool |
|---|---|---|---|---|
| stock fork, TP=2 | 214.8 | 227.8 | 2.51 | 2,044,032 |
| online-MXFP8 runtime, TP=2 | **308.3** | 339.1 | 3.01 | 2,254,464 |
| same, at temp 1.0 / top_p 0.95 / top_k 20 | 274.7 | 313.4 | 2.91 | |

Context 786,432 (YaRN factor 3.0), fp8_e4m3 KV, 8 concurrent, quality 20/20 in
every row.

One methodology note that cost us time: sampling temperature changes speculative
acceptance materially (3.01 at greedy vs 2.91 under real sampling, 308 vs 275
tok/s). Worth stating which one a published figure refers to.

Details, flags and the full table:
https://github.com/untcoder2/qwen38-flash-next-nvfp4-sm120-tp2

---

## 3. jpezzulli — issue

**Куда:** https://github.com/jpezzulli/sglang-rtxpro6000/issues → New issue

**Заголовок:** `Pennyroyal runs at TP=2 (with one flag) — measured numbers on 2x RTX PRO 6000`

**Текст:**

---

`LIMITATIONS.md` scopes this to TP=1, which is fair given one card to test on. For
what it is worth: it runs on two, and faster than the single-card configuration,
because the PLE table stays resident in VRAM instead of being offloaded.

Changes needed, on `next-plain` with an NVFP4 checkpoint carrying a BF16 MTP block:

1. `--disable-custom-all-reduce` — **mandatory**. Without it startup dies:
   `RuntimeError: invalid argument` at `custom_all_reduce.py:241
   register_graph_buffers` → `get_graph_buffer_ipc_meta`. This is almost certainly
   why TP=2 looks broken: it is the first thing you hit and it is fatal.
2. `PLE_ARGS=(--no-ple-offload-embedding)` in `ple-backend.sh`.
3. `--mem-fraction-static` 0.981 → 0.88.
4. Drop `--hicache-storage-backend nixl` (`Failed to create NIXL backend` here).
5. `--max-mamba-cache-size` 24 → 48, otherwise the engine caps concurrency at 4
   regardless of `--max-running-requests` (it says so explicitly in the log, which
   was helpful — thank you).

Result on 2x RTX PRO 6000 Blackwell, context 786,432, KV pool 2,254,464,
8 concurrent, quality 20/20:

| | tok/s median | p90 | accept |
|---|---|---|---|
| 1 card, `next-plain` | 259.1 | 298.9 | 3.06 |
| **2 cards** | **308.3** | 339.1 | 3.01 |
| 2 cards, production sampling | 274.7 | 313.4 | 2.91 |

Two more observations that may interest you:

- **FR-Spec is language-bound.** The bundled 65k map on Russian prompts: 192.2
  tok/s, accept 2.17. Same server, English prompts: 229.8, accept 2.62. But even on
  English it lost to full vocabulary (271.6, accept 3.17) — on this hardware the
  draft step was not the bottleneck, so the trade did not pay.
- **The chat template substitution is worth a line in the docs.** Swapping
  froggeric-v22.5 for the checkpoint's own template cost nothing measurable
  (274.7 vs 275.9) but changes tool-call formatting, which matters for agentic use.

Full write-up: https://github.com/untcoder2/qwen38-flash-next-nvfp4-sm120-tp2

---

## 4. mratsim issue #3 — ответ в тред

**Куда:** https://github.com/mratsim/sglang-qwen38fn-sm120-turbo/issues/3

**Текст:**

---

Data point on patches/0010 against the official `nvidia/Qwen3.8-Flash-Next-NVFP4`
checkpoint, on 2x RTX PRO 6000 Blackwell (SM120), SGLang fork, FlashInfer 0.6.17.

The diagnosis matches ours exactly — the mtp.* experts are declared
`FP8_PB_WO` / `group_size: 128` and get built unquantized, acceptance lands at 1.50
against ~3.0 with a BF16 MTP block.

The patch itself, however, does not survive on our build. With the MoE branch
firing (confirmed by instrumentation — runtime algo string is `FP8_BLOCK_SCALES`,
not the `FP8_PB_WO` the config file contains), the server loads fully, prints
readiness, and dies on the first real batch:

```
RuntimeError: Triton Error [CUDA]: an illegal memory access was encountered
  at scheduler.run_batch
```

Isolated run: single launcher, no supervisor interference. Swapping
`MoeRunnerBackend.TRITON` for `DEEP_GEMM` fails earlier, during graph capture:

```
CUDA driver error 719 (CUDA_ERROR_LAUNCH_FAILED)
  in m_grouped_fp8_gemm_nt_contiguous
```

On this build `flashinfer_cutlass` is the only MoE runner that works, and
`Fp8MoEMethod` has no flashinfer_cutlass path — so the Triton pin may be the
sticking point rather than the branch itself.

We ended up transplanting a BF16 MTP block instead, which sidesteps the whole
question. Writing it up in case someone else hits the same wall:
https://github.com/untcoder2/qwen38-flash-next-nvfp4-sm120-tp2

Separately, your repo's `serve_nv.sh` was the most useful reference we found for
SM120 flag choices — thank you for publishing it.

---

# Порядок и ссылки

| № | площадка | ссылка | действие |
|---|---|---|---|
| 1 | NVIDIA | https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4/discussions | **New discussion** |
| 2 | Qwen #47 | https://huggingface.co/Qwen/Qwen3.8-Flash-Next/discussions/47 | комментарий в тред |
| 3 | jpezzulli | https://github.com/jpezzulli/sglang-rtxpro6000/issues | **New issue** |
| 4 | mratsim #3 | https://github.com/mratsim/sglang-qwen38fn-sm120-turbo/issues/3 | комментарий в тред |

Репозиторий, на который ссылаются все четыре:
https://github.com/untcoder2/qwen38-flash-next-nvfp4-sm120-tp2

**Порядок лучше такой:** сначала 1 и 2 (там цифры, они соберут внимание), потом 3 и
4 — это разработчикам, и там уместно сослаться уже на живое обсуждение.
