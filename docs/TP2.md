# Running the SM120 single-GPU builds at TP=2

Both SM120 runtimes for this model are documented as single-GPU. One states
"TP1 Flash-Next only, not TP2"; the other targets "1x RTX Pro 6000". That is an
honest statement of what each author could test with one card — it is not a
code-level block.

On two cards it runs, and it beats the single-card configuration it was tuned for,
because the model no longer has to evict its PLE table to host memory:

| | 1 card | 2 cards |
|---|---|---|
| tok/s median | 259.1 | **308.3** |
| KV pool | 534,912 | **2,254,464** |
| context | 524,288 | **786,432** |
| PLE table (47.7 GiB) | host RAM | **VRAM** |

## What you have to change

### 1. Disable custom all-reduce — mandatory

Without it, TP=2 dies during startup:

```
RuntimeError: invalid argument
  custom_all_reduce.py:241   register_graph_buffers
  custom_all_reduce_ops.py:63  get_graph_buffer_ipc_meta
```

Add `--disable-custom-all-reduce`. This single failure is why the builds look like
they cannot do TP=2: it is the first thing you hit and it is fatal.

You fall back to NCCL, which costs latency on a box without NVLink. There is
throughput left on the table for whoever fixes the IPC path.

### 2. Keep the PLE table in VRAM

The single-card recipes hard-code `--ple-offload-embedding` because 123.5 GiB of
weights cannot fit in 96 GB. At TP=2 they can, and offloading becomes pure loss:

```bash
# in the shared ple-backend.sh
PLE_ARGS=(--no-ple-offload-embedding)
PLE_OFFLOAD_EMBEDDING=false
```

This also frees roughly 51 GB of host RAM, which is what lets you keep HiCache.

### 3. Lower mem-fraction

`0.981` is a single-card number. On two cards with prefill peaks use `0.88`.
Prefill of a long context needs several GiB of working buffers above the static
allocation, and `nvidia-smi` samples too slowly to show the peak.

### 4. Drop the NIXL storage backend

`--hicache-storage-backend nixl` fails with `Failed to create NIXL backend` unless
its filesystem prerequisites are met. HiCache itself works fine without it; you
lose only cache persistence across restarts. Remove that flag and its
`--hicache-storage-backend-extra-config` companion.

### 5. Size the Mamba state cache for your concurrency

Passing `--max-running-requests 8` is not enough. The engine tells you why:

```
max_running_requests is capped to 4 by the mamba state cache
(max_mamba_cache_size=24, 5 state slots per request)
```

Five slots per request, so eight concurrent requests need at least 40 slots. We set
`--max-mamba-cache-size 48`. Cost: about 1.3 GB of VRAM and 157k KV tokens.

### 6. Match the context stretch to the context length

If you raise `--context-length`, raise the YaRN factor with it or the two disagree.
For 786,432 from a 262,144 base that is `"factor": 3.0`, not the default 2.0, plus
`"max_position_embeddings": 786432` in the override.

### 7. Consider the chat template

One of these images substitutes its own 28 KB chat template for the checkpoint's
native 8.9 KB one. That is a deliberate choice by its author, but it changes how
tool calls and reasoning get formatted. To match the checkpoint's own behaviour,
point the template variable at the checkpoint's `chat_template.jinja`. Costs
nothing measurable: 274.7 vs 275.9 tok/s.

## Resulting command

```
--tp 2 --disable-custom-all-reduce --dtype bfloat16 --quantization modelopt_fp4
--kv-cache-dtype fp8_e4m3 --mem-fraction-static 0.88 --context-length 786432
--page-size 64 --max-running-requests 8 --chunked-prefill-size 4096
--mamba-radix-cache-strategy extra_buffer --mamba-ssm-dtype bfloat16
--max-mamba-cache-size 48 --gdn-mtp-cache-mode none --mamba-track-interval 64
--linear-attn-decode-backend flashinfer --linear-attn-prefill-backend flashinfer
--enable-hierarchical-cache --hicache-size 32 --hicache-host-memory-mode cache
--no-ple-offload-embedding
--speculative-algorithm NEXTN --speculative-num-steps 3 --speculative-eagle-topk 1
--speculative-num-draft-tokens 4
```

## One coupling worth knowing

`--mamba-ssm-dtype bfloat16` is not optional if you want
`--linear-attn-decode-backend flashinfer`; the engine refuses the combination with
float32 outright:

```
--linear-attn-decode-backend flashinfer on SM100+ requires
--mamba-ssm-dtype bfloat16, got 'float32'
```

Some speculative recompute modes force float32 SSM state, and those lock you out of
the fast decode backend. We measured both on this box and the difference was inside
noise: accept 2.50 vs 2.52, 206.7 vs 214.8 tok/s. Do not assume either way for your
hardware; measure.
