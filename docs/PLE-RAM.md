# PLE placement, revisited: host-pinned RAM at TP=2

> **Corrects [TP2.md §2](TP2.md).** That section claimed offloading the PLE table
> at TP=2 is "pure loss." True on the v2.5.x engine at `mem-fraction 0.88`; on
> v3.0.1 it reversed. We published the old claim, so we publish the correction.

## TL;DR

Moving the 47.7 GiB PLE n-gram table from VRAM to **host-pinned CPU memory** on
Pennyroyal v3.0.1 grew the KV pool **2,752,640 → 6,425,920 tokens (×2.3–2.6**
depending on which mf-0.95 boot you compare to), cost **nothing measurable** in
prefill or decode on this box, and let the host KV tier shrink to the memory
left over rather than memory budgeted for it.

## Flag semantics (v3.0.1 help text, verbatim)

```
--ple-offload-embedding, --no-ple-offload-embedding
    Offload Qwen4 PLE n-gram embedding weights to CPU pinned memory. Enabled
    by default for BF16 Qwen4-Exp on CUDA; use --no-ple-offload-embedding to
    disable.
```

Two traps we walked into and out of:

- The runtime's shared `ple-backend.sh` names its default branch `ram` while
  its `ram` branch passes `--no-ple-offload-embedding` — i.e. the *table* is
  RAM-addressable but the *weights* stay in VRAM. Read the flag, not the label.
- The launch script feeds `--field "ple_offload_embedding=$PLE_OFFLOAD_EMBEDDING"`
  into the NIXL cache namespace. Flip the variable along with the flag or two
  different placements share one cache directory (same class of drift we hit
  with `max_mamba_cache_size`; see TP2.md §5 update).

## What we changed

- `PLE_ARGS=(--ple-offload-embedding)`, `PLE_OFFLOAD_EMBEDDING=true`
- `--hicache-size` 24 → 4 for the first boot (deliberate headroom for the new
  48 GiB pinned allocation on a 90 GB host), then staged 4 → 10 — sized to the
  leftover, see policy below
- everything else untouched: TP=2, mf 0.95, context 786,432 (YaRN×3), mrr 16,
  mamba cache 128, fp8 KV, adaptive NEXTN

Operationally: backup both files, drop the host page cache (`echo 3 >
/proc/sys/drop_caches`) **before** the restart — the pinned allocation is checked
against `MemFree`, not `MemAvailable`, and 65 GiB of checkpoint-read page cache
will make a healthy machine look full; then restart through the supervisor and
gate on health with an automatic rollback if it never arrives.

## Measurements (single engine, before/after)

| | before (v3.0.1, PLE in VRAM, hicache 24) | after (v3.0.1, PLE pinned in RAM, hicache 4) |
|---|---|---|
| health | — | up at 220 s |
| KV pool | 2,752,640 | **6,425,920** |
| VRAM used | ~85–88 GiB/card | **93,690 / 97,887 MiB/card**, `available_gpu_mem` 3.58 GB |
| cold prefill 169,608 tok | 13.0 s (~13.1k tok/s, era reference) | **12.55 s (~13.5k tok/s)** |
| warm re-prefill | 0.42 s | **0.34 s** (100.0 % radix hit) |
| short_bench greedy median | 332 (v2.5.1 era reference) | **405.6** (n=5: 336/467/412/406/404) |
| retract since boot | 0 | 0 |

**Attribution honesty:** the "before" reference rows come from earlier engine
versions (v2.5.1/v2.5.3); between then and now the box also gained v3.0.1
kernels and adaptive NEXTN. We did **not** A/B PLE placement within one engine
build. What this actually proves: the move to host-pinned RAM **regressed
nothing measurable**, and the KV ×2.6 is its direct mechanical consequence.
The +22 % median vs the old reference belongs mostly to the engine, not to PLE.

## Why PCIe cost nothing here

PLE is looked up once per token as a single row of ~2.5 KB (hidden 2560, FP8
storage). At 13.5k tok/s that is ~35 MB/s — two orders of magnitude under the
link, and the gather overlaps a compute-bound MoE prefill. The VRAM copy was
never paid for by speed; it was paid for in KV pool: 24 GiB per card, silently.

The failure mode to respect is the **host**, not the bus: 48 GiB pinned is
non-evictable, and a cold-ish host (page cache dropped) is required at load
time. If you run other workloads on the inference host, budget for it.

## HiCache policy inversion

Under the old layout the freed host RAM was the point — VRAM PLE "is what lets
you keep HiCache," and we sized the host tier at 24–32 GiB. With a 6.42M-token
device pool the L2 tier's job (catching evictions) nearly disappears, so the
sizing question flips: HiCache should now be **whatever the host can spare**
(we landed on 10 GiB), not a budgeted tier. The engine's boot warning
"HiCache host KV pool is smaller than the device pool" is expected under this
policy and cosmetic.

## Mamba note

`--max-mamba-cache-size 128` stays: measured occupancy is ~7.75 slots per
request (admission arithmetic says 5; dialog-branch snapshots make up the
difference), so mrr 16 needs ~124. Shrinking it to claw RAM back would cap
concurrency at a fraction of what the new KV pool can actually serve.

## Rollback

Restore the two backup files, restart through the supervisor, gate on health.
One command, no data change — placement is a launch-time decision, the
checkpoint on disk is identical.

## The return (2026-10-26): the mount table is the source of truth

Trying to flip the flag back on v3.1.1 failed **silently** at first: since the
23.10 upgrade the container bind-mounts only the launch recipe and the chat
template — `ple-backend.sh` is *not* mounted anymore, so the image's stock file
(default `ram`, i.e. `--ple-offload-embedding`) wins regardless of what the host
copy says. The half-applied change died exactly as this doc warned it could:
`Not enough host memory. Requesting 19.71 GB` — the hicache raise arrived, the
PLE flip did not. Check `docker inspect --format '{{json .Mounts}}'` before
editing any config you assume "host copy == container copy".

Working lever on v3.1.1 — override in the mounted recipe, immediately *after*
the source (anything set before it is overwritten):

```bash
if [[ "${PENNY_PLE_BACKEND:-ram}" == ram ]]; then
  PLE_ARGS=(--no-ple-offload-embedding)   # PLE in VRAM
  PLE_OFFLOAD_EMBEDDING=false             # keep the NIXL cache-identity field honest
fi
```

The service restart recreates the container (`docker rm` + `run`), so the
single-file bind re-resolves and in-place host edits do land — but verify the
live `/proc/<pid>/cmdline` element-wise, not only health: that night health
would have returned green on the rolled-back profile. Measured after the return
(mf 0.93, chunk 8192, HiCache 30): list-style bench 414.6, KV pool 2,746,496,
host freed of the ~48 GiB pin, 0 retracts / 0 OOM through soak and bench — see
[MEASUREMENTS.md](MEASUREMENTS.md).
