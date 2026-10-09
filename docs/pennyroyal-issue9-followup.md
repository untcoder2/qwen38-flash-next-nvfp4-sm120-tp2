# Follow-up comment for jpezzulli/sglang-rtxpro6000 issue #9
# (post as untcoder2 after login; target: https://github.com/jpezzulli/sglang-rtxpro6000/issues/9)

**Follow-up: v2.5.3 gauntlet passed + a 768K config change + three field notes worth fixing**

Keeping the promise from my earlier post — v2.5.3 now runs on the same production rig
(2× RTX PRO 6000 96 GB, `next-plain`, our TP=2 overrides mounted read-only, PLE-in-VRAM,
online MXFP8, NVFP4 checkpoint + BF16 re-sharded MTP head, NEXTN 3/1/4, HiCache host tier 24 GB):

- all six verify gates green after the switch (server_info, file hashes vs our golden
  snapshot, template-400, effort effect, accept/retract, metrics);
- effort tiers behave exactly as documented on our chat template: `low=674` vs `xhigh=5427`
  thinking tokens on the same prompt, bit-for-bit reproducible across repeats;
- accept-length median 2.25–2.6, retract **0** under continuous multi-agent load;
- `--mem-fraction-static 0.95` is stable for us (no OOM/Xid, ~93/98 GB per GPU, ~5 GB headroom
  at boot): KV pool grew 2 445 440 → 2 752 640 tokens, single-stream greedy median 326–333 tok/s;
- radix/hicache hit ≈ 98.7 % for repeating agent prefixes (from
  `uncached_prompt_tokens_histogram` vs `prompt_tokens_total`).

Since then we A/B-ed our own workload: YaRN×4 @ 1 048 576 → YaRN×3 @ 786 432 (+ mf 0.92→0.95,
`--max-running-requests` 8→16). Three observations that might save other users days:

1. **`--mem-fraction-static` and the fixed `--hicache-size` silently fight.** Raising mf to 0.95
   pushed the device pool (2 752 640 tok) past the host tier (24 GiB = 2 695 872 tok) and every
   boot now logs "HiCache kv host pool is smaller than the device pool; L2 effectiveness is
   reduced". Consider deriving the default host tier from the actual device-pool size (or a
   setup-time consistency check) — right now raising mf quietly degrades the very tier that
   makes long agent prefixes cheap.
2. **Hand-maintained NIXL namespace `--field` entries drift from real launch args.** Our fork
   carried `--field "max_mamba_cache_size=24"` for weeks while the CLI actually ran
   `--max-mamba-cache-size 128` — cache identity should be derived from the parsed server args
   rather than a duplicated literal list, otherwise different configs share one storage dir
   unnoticed. (Makes #32/#22 (host-side config files) even more valuable.)
3. **`--max-running-requests 8` is too tight for agentic serving.** Head + 7 concurrent agents
   saturate it exactly; the 9th request (agent resume, parallel tool round) queues behind the
   running batch even with a fully warm prefix — which looks like a model stall in the client.
   16 fixed it for us; maybe worth documenting an "agentic" preset alongside the single-user
   recipe.

One unproven suspicion that your own pipeline may have already caught: at YaRN×4/1M we saw
mid-decode freezes near the length cap during long effort=max sessions; after moving the whole
stack to 768K with a client ceiling well below the window it disappeared. The mechanism looks
exactly like what **#27 ("Reserve context space for speculative lookahead")** addresses —
happy to test it against our gauntlet when the RC ships.

And thanks for the 3.0 line — the adaptive NEXTN width series (#51–#55) and the
speculative-Mamba memory fixes hit our exact critical path. Standing by to field-report.

— untcoder2
