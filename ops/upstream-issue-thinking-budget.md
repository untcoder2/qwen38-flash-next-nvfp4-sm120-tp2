## Summary

`/v1/messages` rejects `thinking: {"type": "enabled"}` without `budget_tokens` with a 400 — but the serving layer then documents that `budget_tokens` is **not enforced at all** ("the backend has no hard-cap knob to honor it; the serving layer logs a WARNING"). Clients that don't run against Anthropic's real API (which has adaptive-thinking semantics of its own) send exactly this shape and get hard-failed for a field the server ignores anyway.

Observed from ZCode 3.16.3 (desktop agent, Anthropic-compatible provider mode): every main-turn request carried

```json
"thinking": {"type": "enabled"},
"output_config": {"effort": "max"}
```

and died with:

```json
{"type":"error","error":{"type":"invalid_request_error",
 "message":"thinking: Value error, thinking.budget_tokens is required when thinking.type is 'enabled'"}}
```

## Where

`python/sglang/srt/entrypoints/anthropic/protocol.py` → `AnthropicThinkingParam._validate_thinking_shape`:

```python
if self.type == "enabled":
    if self.budget_tokens is None:
        raise ValueError("thinking.budget_tokens is required when thinking.type is 'enabled'")
```

while the class docstring (and serving) already define `adaptive` as the "enabled without a knob" equivalent and log non-enforcement of any budget.

## Proposal (one line each)

- Treat `enabled` without `budget_tokens` as the existing `adaptive` variant (documented identical behavior), **or** default it to `min(max_tokens // 2, 8192)` and keep the WARNING.
- Optional: return that validation error as a `422` with a pointer to the accepted shapes — the current 400 wording is easy to miss in client logs.

## Repro (any SM120 box, v3.1.1, no patches)

```bash
curl -sk https://HOST:PORT/v1/messages -H 'content-type: application/json' -H 'x-api-key: t' \
  -d '{"model":"<served>","max_tokens":256,"thinking":{"type":"enabled"},
       "messages":[{"role":"user","content":"say ok"}]}'
```

Returns 400 on v3.1.0/v3.1.1; with the fix, should stream thinking as `adaptive` does today.

## Impact on us

We keep a thin sidecar in front of the server that patches `budget_tokens` in transit; happy to drop it once the validation relaxes. Anthropic's SDK itself serializes all three variants client-side, so only third-party-compat users hit this.
