# Pennyroyal v2.5.2 / v2.5.3 — TP2 field verification, 2× RTX PRO 6000 (SM120)

Hi! We are the team behind the TP=2 field report (issue #9, the one with
`--disable-custom-all-reduce` + PLE-in-VRAM that shipped into your guidance and
LIMITATIONS). Seeing "TP2 verification is pending" in the v2.5.2 notes — consider
this the second datapoint, from a production rig.

**Rig:** 2× RTX PRO 6000 Blackwell 96 GB, open kernel module 595.84, Ubuntu 26.04,
container `ghcr.io/jpezzulli/sglang-rtxpro6000` running `next-plain`, our TP=2
overrides mounted as configs (same launch args as your qualified recipe plus:
TP_SIZE=2, `--disable-custom-all-reduce`, `--no-ple-offload-embedding`,
`--mem-fraction-static 0.92`, YaRN×4 → 1 048 576 context, NVFP4 official
checkpoint + BF16 re-sharded MTP head, EAGLE/NEXTN 3/1/4, HiCache host tier 24 GB).

**v2.5.1 → v2.5.2 upgrade result (48 h non-stop multi-agent load):**

- Boot: 200–210 s to ready, zero OOM/CUDA/Xid lines, no crash-loops;
- 10 324 timed requests: retract = **0** across all buckets (this also retires
  my earlier caution that 0.92 mem-fraction might induce retractions — it did not);
- decode per lane p50 ≈ 190–200 tok/s, single-stream greedy median 313 tok/s;
- cold prefill by prompt size: 12.8K tok/s (64–256K), 8.8K (256–655K), 5.7K (>655K);
- radix/hicache hit ratio 96–100 % for repeating agent prefixes;
- long-context retrieval: NIAHS at 362K — 4/4 depth probes + 20/20 multi-needle,
  identical before/after the upgrade;
- the new `derive_namespace.py` tp_size validation and `PENNY_REASONING_EFFORT`
  helper both work as documented for us; unknown-tool forwarding matches the
  behavior we had hand-pinned via `SGLANG_FORWARD_UNKNOWN_TOOLS=true`.

**v2.5.3** — the MTP accepted-token-boundary fix (#17) lands exactly on our
critical path (native NEXTN + GDN recurrent state). Image is being pulled now;
we will run our regression gauntlet (verify 6-gates: server_info, file hashes,
template-400, effort effect, accept/retract — plus NIAHS and the speed suite)
on 2.5.3 this week and can report numbers here as a follow-up.

Thank you — and thanks to u/StockSpecialist1707 — for picking up the TP=2 path
and for the release discipline (CHANGES.md with upstream PR mapping made our
triage trivial). Our measured baselines and the artifact-hygiene notes are
public here: https://github.com/untcoder2/qwen38-flash-next-nvfp4-sm120-tp2

— untcoder2
