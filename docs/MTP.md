# The fp8 MTP block in the official NVFP4 checkpoint

## Symptom

Serve `nvidia/Qwen3.8-Flash-Next-NVFP4` with NEXTN speculative decoding and draft
acceptance sits at **1.50** instead of the ~3.0 the same hardware reaches with a
BF16 draft head. Nothing errors. Nothing warns. You simply lose about 40% of decode
throughput and never find out why.

| checkpoint | accept len (median) | tok/s (median) |
|---|---|---|
| official NVFP4, its own fp8 MTP | 1.50 | 124.8 |
| official weights + BF16 MTP transplant | 2.51 | 214.8 |

Reproduced independently in a **second, unrelated runtime** on the same box:
1.50 with the official checkpoint, 3.06 with the transplant. This is a property of
the checkpoint, not of one engine.

## What is actually in the checkpoint

```
tensors total .................. 299,545
mtp.* tensors ..................   3,101
weight_scale_inv tensors .......   1,536   (every one of them inside mtp.*)
mtp.* dtypes ................... 1,565 BF16 + 1,536 F8_E4M3
```

and in `config.json`:

```json
"quantized_layers": {
  "mtp.layers.0.mlp.experts": {"quant_algo": "FP8_PB_WO", "group_size": 128}
}
```

The draft MoE experts are serialised as fp8 with 128x128 blockwise
`weight_scale_inv` scales.

## Why it collapses

The chain has three links and the middle one is missing:

1. **Detect that MTP is quantised.** Works, provided the loader consults
   `quantized_layers` instead of assuming `modelopt_mixed` keeps MTP in bf16.
2. **Build a MoE method for that algorithm.** *Missing.* In
   `ModelOptMixedPrecisionConfig.get_quant_method()` the linear branch handles
   `FP8_PB_WO`, but the MoE branch only knows `FP8`, `MXFP8` and `NVFP4`. The
   experts fall through and are built **unquantized**.
3. **Map the block scales onto the parameters.** Never reached.

An unquantized MoE that then receives fp8 weights loads them without their scales.
No crash, no NaN, just a drafter emitting noise which the target model dutifully
rejects. Hence acceptance near 1.

### A trap when you go looking

The config says `FP8_PB_WO`, and reading it offline confirms that:

```python
cfg._resolve_quant_algo("mtp.layers.0.mlp.experts")   # -> 'FP8_PB_WO'
```

At **runtime** the same call returns `FP8_BLOCK_SCALES`; the name is normalised
somewhere in the load path. We lost hours writing a fix keyed on the wrong string.
Instrument it and read what the running process actually sees:

```
[diag] prefix='mtp.layers.0.mlp.experts' algo='FP8_BLOCK_SCALES' layer=FusedMoE
```

## The circulating patch does not work on SM120

A community patch adds the missing MoE branch and pins the Triton runner. On this
checkpoint it does fire, and then the server **dies on the first real batch**:

```
RuntimeError: Triton Error [CUDA]: an illegal memory access was encountered
  at scheduler.run_batch
```

Reproduced in isolation: single launcher, no supervisor races, model fully loaded,
`The server is fired up and ready to roll!` printed, crash on the first request.

Swapping the pinned runner to DeepGEMM (the natural choice for 128x128 blockwise
fp8) fails earlier, during CUDA graph capture:

```
CUDA driver error 719 (CUDA_ERROR_LAUNCH_FAILED)
  in m_grouped_fp8_gemm_nt_contiguous
```

On this build the only MoE runner that works at all is `flashinfer_cutlass`, and
`Fp8MoEMethod` has no flashinfer_cutlass path. There is currently **no working MoE
runner for the official fp8 MTP on SM120**.

## What does work: transplant a BF16 MTP head

Take the 31 BF16 `mtp.*` tensors from a community BF16 re-release, write them as a
new shard, and rebuild the index so it points at your shard for `mtp.*` and at the
untouched NVIDIA files for everything else. NVIDIA's weights are not modified;
verify by inode if you care.

Result: 296,475 tensors, 31 MTP tensors all BF16, **zero** `weight_scale_inv`.
Acceptance goes 1.50 to 3.0, throughput 124.8 to 214.8 on a stock fork and to
308.3 with an online-MXFP8 runtime at TP=2.

## Quality is not affected either way

Worth stating, because it changes what you should worry about. With exact
verification (`speculative_accept_threshold_*` at 1.0) the target model validates
every drafted token against its full vocabulary, so a broken drafter costs speed
and nothing else. We measured 20/20 on a verifiable-answer set in **every**
configuration, including the one with acceptance 1.50.

The caveat: that guarantee holds only if the draft-verify implementation is
correct. There is a credible field report of repetition loops traced to that exact
path, so treat "lossless" as a property to verify rather than assume.

## UPDATE 2026-10-19: NVIDIA answered with a QAD checkpoint, and the
## community transplant became unnecessary

NVIDIA shipped [`nvidia/Qwen3.8-Flash-Next-FP4-Local`](https://huggingface.co/nvidia/Qwen3.8-Flash-Next-FP4-Local):
the same base quantized by quantization-aware distillation, in which the MTP
block is NVFP4 (`W4A16_NVFP4`, group 16) with **block scales stored directly**
(no CSF compression — that encoding applies to the main model only). Because
NVFP4 (E2M1 value x E4M3 per-16 scale) round-trips into BF16 exactly through
FP32 intermediates, you can materialize the trained head yourself:

- dequantize the 4,608 per-expert tensors (`weight` U8 pairs, low nibble first,
  sign = bit 3; `weight_scale` F8_E4M3 in **linear** [out, in/16] layout —
  verified there is no 128x4 swizzle in ModelOpt-serialized checkpoints;
  `weight_scale_2` F32 is the decode **multiplier**, do not invert);
- concat gate‖up per expert into `[512, 1280, 2560]`, stack down into
  `[512, 2560, 640]`, and reuse the 29 raw BF16 tensors verbatim — exactly the
  31-tensor transplant layout described above;
- serve with `--speculative-draft-model-quantization unquant` as before.

Measured on the same TP=2 RTX PRO 6000 pair: short_bench median **405.6 →
412.5**, greedy-window accept **3.01 → 4.51**, quality set still 20/20. EOS
behaviour is the distillation's stated win: under an adversarial long-reasoning
probe (8 prompts, temp 1.0) we saw zero empty completions at both 8K and 32K
token budgets; one repetition burst in eight, ended by a natural stop.

So the general recipe is: any QAD NVFP4 MTP head can be BF16-materialized into
your checkpoint tree with no engine changes — you no longer need a third-party
re-release for the head.

## Ask for NVIDIA

Partially answered on 19.10: `FP4-Local` ships a QAD-trained MTP block (see
update above) — but the original `nvidia/Qwen3.8-Flash-Next-NVFP4` still ships
the fp8 `FP8_PB_WO` draft that every SM120 runtime silently loads wrong. Either
re-ship that checkpoint with a BF16 or QAD-NVFP4 MTP block, or land a working
MoE runner path for `FP8_BLOCK_SCALES` on SM120. As shipped, every user who
enables NEXTN on it silently loses roughly half their decode throughput.
And to draft-quant loaders: accept serialized-NVFP4 MTP natively, this manual
dequant should not be a user exercise.
