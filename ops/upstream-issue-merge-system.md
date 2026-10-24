## Summary

Agentic clients increasingly send **multiple `system` messages** (agent persona + rules + mid-conversation reminders — Anthropic permits reminders anywhere; ChatML templates do not). With Qwen/NVIDIA ChatML checkpoints the failure is cryptic and hard to attribute:

```json
{"object":"error","message":"System message must be at the beginning.","type":"BadRequest","code":400}
```

It is not raised by any sglang validator — it comes from the checkpoint's Jinja template
(`chat_template.jinja`, `raise_exception` under `not loop.first`), so the server-side
contract is effectively: **at most one system message, at index 0**.

We traced it end-to-end from the 400 body through `docker logs` into the shipped
template. Two lines of server-side normalization would make a whole class of
third-party agents work out of the box (in our case: a client that emits 3 leading
system messages + tool schemas fails every first turn until proxied).

## Request

A launch-time compat flag, e.g.

```
--chat-compat-merge-system-messages
```

that, before templating, merges all `role=system` messages into one (join with `\n\n`,
preserving relative order, hoisted to index 0). Off by default; zero impact on
OpenAI-canonical traffic. Two optional niceties while we're here:

- hoist **mid-conversation** system messages into the merged one too (Anthropic-style
  reminders), since the template rejects those even harder;
- make the template/validation error name its origin, e.g.
  `"message": "chat template (Qwen/NVIDIA ChatML): multiple system messages …"` —
  today it reads like an sglang rule and sends users grepping the wrong repo (us included).

## Why server-side rather than template-side

The template is checkpoint-pinned (its sha is part of cache identity / NIXL namespace
by design), and the constraint is arguably correct for the model's training format —
what's missing is a *normalization boundary* for well-intentioned clients, which is
exactly the layer server_args already owns (`--reasoning-parser`, `--chat-template`,
the new `trust_request_chat_template` gate, etc.).

## Repro (v3.1.1, any Qwen4Exp/Flash-Next checkpoint)

```bash
curl -sk https://HOST:PORT/v1/chat/completions -H 'content-type: application/json' \
  -d '{"model":"<served>","max_tokens":32,"messages":[
     {"role":"system","content":"A-rule."},
     {"role":"system","content":"B-rule."},
     {"role":"user","content":"say ok"}]}'
```

400 today; with the flag, one merged system → 200. Single-system requests unchanged.

## Field note

ZCode 3.16.x (OpenAI format) sends 2–3 system messages per main turn and one
`system` reminder mid-history; we currently keep a sidecar that merges+hoists +
strips Anthropic-only fields (`cache_control`, un-budgeted `thinking`) in transit.
We would rather delete it — the flag would subsume it for everything except the
`/v1/messages` shape fix (separate issue, filed today).
