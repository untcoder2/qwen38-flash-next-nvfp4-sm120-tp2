# Field report: Pennyroyal v2.5.2 → v2.5.3 under production load

Rig: same two-card SM120 box as the rest of this repo (2× RTX PRO 6000 96 GB, no
NVLink, driver 595.84), our TP=2 overrides mounted into the official container,
NVFP4 checkpoint + BF16 re-sharded MTP head, NEXTN 3/1/4 (EAGLE verify), HiCache
host tier 24 GiB + NIXL FILE storage. The engine ran our agent workload 24/7
throughout — a head session plus a fleet of concurrent sub-agents.

## v2.5.2 — 48 h soak

- boot to ready 200–210 s, zero OOM/CUDA/Xid lines, no crash-loops;
- 10,324 timed requests: **retract = 0** across all buckets;
- decode per lane p50 ≈ 190–200 tok/s, single-stream greedy median 313 tok/s;
- cold prefill by prompt size: 12.8K tok/s (64–256K), 8.8K (256–655K), 5.7K (>655K);
- radix/hicache hit 96–100 % for repeating agent prefixes;
- NIAHS at 362K: 4/4 depth probes + 20/20 multi-needle, identical before/after;
- the new `derive_namespace.py` tp_size validation and the `PENNY_REASONING_EFFORT`
  tier helper both behaved as documented.

## v2.5.3 — regression gauntlet

Run through our six read-only gates (see [ROLLBACK.md](ROLLBACK.md)): all green
after the switch. Highlights:

- effort tiers on our chat template: `low` → 674 thinking tokens vs `xhigh` → 5,427
  on the same prompt, bit-for-bit reproducible across repeats;
- accept-length median 2.25–2.6, retract 0 under continuous agent load;
- `--mem-fraction-static 0.95`: stable, ~93/98 GB per GPU, KV pool 2,445,440 →
  2,752,640; single-stream greedy median 326–333 on a quiet engine;
- warm re-prefill of a 169,608-token prompt: 13.41 s cold → 0.39 s warm (100.0 %
  radix hit) — the number to beat if you are evaluating cache tiers;
- NIAHS at ~362K post-switch: 4/4 depth probes + 20/20 multi-needle again.

## The YaRN×4 detour

With v2.5.3 we stretched context to 1,048,576 (YaRN factor 4.0 over the 262,144
base, `max_position_embeddings` to match) and ran it for days. We moved back to
786,432 / factor 3.0. Reasons, measured:

1. mid-decode freezes near the length cap during long high-effort sessions — the
   mechanism looks like speculative lookahead not fitting inside `context_len`,
   which is exactly what the upstream PR we tracked as #27 addresses (unproven on
   our side until we can test the RC; the 768K config keeps us far from the edge
   and the freezes disappeared);
2. agent-visible quality at 200K+ was no better than ×3 for our workload, and the
   ×4 window doubled the KV bookkeeping ceiling;
3. server-side prefix-cache hit on the fleet stabilised at ≈ 98.7 % (computed from
   `uncached_prompt_tokens_histogram` vs `prompt_tokens_total`, not client-reported
   usage — engines do not always surface cached tokens to clients, so bill-side
   cache accounting needs the server metrics).

## Three upstream notes (also filed on jpezzulli/sglang-rtxpro6000 issue #9)

1. **`mem-fraction` and a fixed `hicache-size` fight silently.** At mf 0.95 the
   device pool outgrew the 24 GiB host tier and every boot logs "host pool is
   smaller than the device pool; L2 effectiveness is reduced". Derive the host
   tier from the device pool, or check the pair at setup.
2. **Hand-maintained NIXL namespace `--field` literals drift from CLI args.**
   Ours said `max_mamba_cache_size=24` for weeks while the flag ran 128 — cache
   identity should come from parsed server args, not a duplicated list.
3. **Concurrency presets for agentic serving.** `max-running-requests=8` plus the
   mamba-slot cap made "head + 7 agents" queue the 9th request behind the batch
   even with a warm prefix; 16 requests / 128 mamba states fixed it. An
   "agentic" recipe or a startup line summarising the effective concurrency floor
   would save users the archaeology.

Status: running v2.5.3 on the 768K profile; watching the v3.0 RC line (adaptive
NEXTN width #51–#55, speculative-Mamba memory fixes). We will field-test the RC
through this same gauntlet when it ships.
