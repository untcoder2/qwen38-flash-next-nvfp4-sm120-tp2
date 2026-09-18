# Artifact hygiene: fixing the checkpoint, not the flags

Context: [MTP.md](MTP.md) documents the defect — the official NVFP4 checkpoint's
`hf_quant_config.json` declares `mtp.layers.0.mlp.experts` as `FP8_BLOCK_SCALES`
(128x128 blockwise scales) and loaders that trust that map build the draft MoE
wrong, collapsing NEXTN acceptance to ~1.5. The transplant of a BF16 MTP block
fixes the weights; a launch flag (`--speculative-draft-model-quantization
unquant`) forces the loader to treat the draft as unquantized.

## The finding

Those two fixes are redundant, and the *map* is the thing that still lies. After
the BF16 transplant the tensors on disk are BF16, but the quantized_layers map
keeps the `mtp.*` entry. Engine-side handling of that entry has already bitten
one community fork (see [mratsim sglang-qwen38fn-sm120-turbo
issue #3](https://github.com/mratsim/sglang-qwen38fn-sm120-turbo/issues/3)), and
future loader changes that "respect the map more" could resurrect the bug
against correctly-transplanted weights.

The clean fix is one-time, on the artifact:

1. Remove every `mtp.*` key from `quantized_layers` in `hf_quant_config.json`
   (check `config.json`'s `quantization_config.quantized_layers` too; on our
   checkpoint the entries lived only in `hf_quant_config.json`).
2. Remove the `--speculative-draft-model-quantization unquant` workaround from
   the launch flags.
3. Boot and verify.

## Verification (v2.5.1, 2x RTX PRO 6000, TP=2, 2026-09-19)

Deterministic probe — ask for a perfectly predictable continuation, greedy, and
read acceptance from the engine's own decode log:

```bash
curl -s http://localhost:8000/v1/chat/completions -H 'content-type: application/json'   -d '{"model":"qwen3.8-flash-next","messages":[{"role":"user","content":"List the numbers 1 to 400 in order, one per line, nothing else."}],"max_tokens":3000,"temperature":0}' -o /dev/null
docker logs penny --since 60s 2>&1 | grep 'Decode batch' | grep -o 'accept len: [0-9.]*' | sort | uniq -c
```

Healthy (map cleaned, no flag): `accept len: 4.00` — the NEXTN ceiling
(3 steps, 4 draft tokens), repeatedly. Broken: pinned 1.5-1.6 from the first
token after boot. Quality was 20/20 on the verifiable set in both the flagged
and unflagged configuration, exactly as MTP.md predicts for anything with a
working verifier.

## Byte-identity with the community re-shard

[dicksondickson/Qwen3.8-Flash-Next-NVFP4-reshard-mtp-fix](https://huggingface.co/dicksondickson/Qwen3.8-Flash-Next-NVFP4-reshard-mtp-fix)
is an independent BF16-MTP re-shard of the same official checkpoint. We compared
it to our own transplant tensor by tensor: parse the safetensors header of his
MTP shards (`model-00138..00141`, ~6 GB) and of our added BF16 MTP file, sha256
each tensor's byte range, diff the sets.

```
n_mine: 31, n_his: 31, common: 31, byte_identical: 31
diff_only_mine: []  diff_only_his: []  mismatch_content: []
```

All 31 `mtp.*` tensors are bit-for-bit equal, and his quant-map hygiene matches
ours (`mtp.*` absent from both `hf_quant_config.json` and `config.json`). Two
groups performing the same surgery independently landed on identical bytes —
treat that as evidence the transplant is the canonical repair for this
checkpoint, and that a server-side A/B of the two artifacts would be a
comparison of the same file in different packaging.

## Operating notes

Do the artifact edit as a verified step, not blind: snapshot the live engine
before (image digest-pinned, config hashes, `/proc` command lines), edit,
restart through the supervisor, then re-verify — [tools/](../tools) and
[ROLLBACK.md](ROLLBACK.md) are exactly this loop. Anything that fails a gate
rolls back in one command instead of an investigation.
