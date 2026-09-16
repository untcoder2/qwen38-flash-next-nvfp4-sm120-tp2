# FR-Spec: the reduced draft vocabulary is language-bound

FR-Spec restricts the *draft* model to the most frequent N token IDs (65,536 here)
so each speculative step costs less. The target model keeps its full vocabulary, so
output quality is unchanged by construction — we measured 20/20 with and without.

What is not obvious is how much the map's provenance matters, and that a
well-matched map still may not win.

## Measured

Same server, same checkpoint, same hardware, one card. Only the prompt language and
the profile change:

| | Russian prompt | English prompt |
|---|---|---|
| full vocabulary | **259.1 / 263.2** · accept 3.06 / 3.08 | **271.6** · accept **3.17** |
| FR-Spec (bundled 65k map) | 192.2 · accept 2.17 | 229.8 · accept 2.62 |

Two readings:

**The map is language-bound.** Russian 192.2 to English 229.8, accept 2.17 to 2.62.
The drafter cannot propose tokens outside its map, so on text whose tokens the map
does not cover it proposes poorly and verification rejects. Meanwhile the
full-vocabulary drafter barely notices the language at all: 259 vs 272.

**Even on its home turf FR-Spec lost.** English FR-Spec (229.8) is below Russian
full-vocabulary (259.1). Which follows from what FR-Spec is: a reduced vocabulary
can only lose acceptance relative to the full one, never gain it. It buys a cheaper
draft step and pays in accept. On this hardware the draft head was not the
bottleneck, so the trade was a loss.

## Why rebuilding the map for your language is probably not worth it

We considered rebuilding the map from a corpus of our own traffic. The ceiling for
that effort is the full-vocabulary number — you can approach accept 3.08 from
below, not exceed it. Unless your draft step is genuinely bandwidth-bound, the
upside is bounded by zero.

If you do try it, note that a map is a list of integer token IDs and is only
meaningful against one tokenizer's ID mapping. Using a map built for a different
tokenizer means restricting the drafter to a set of essentially random strings:
acceptance collapses toward 1, though output quality still holds because the target
verifies over its full vocabulary. Runtimes that pin the map together with a
reference tokenizer and a checksum are doing the right thing.

Also worth checking before you invest: how representative your corpus actually is.
Ours turned out to be 40 conversations left over from an abandoned experiment.
