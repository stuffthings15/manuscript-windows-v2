# E2B against E4B, twenty essays

Measured 2026-08-06 on an idle machine, mode `balanced`, the locked
`test-corpus/animal-pet-20`, one pack pinned per run with the scorer's new
`--pack` option. Reports: `build/quality-e2b.md`, `build/quality-e4b.md`.

| | E2B | E4B |
|---|---|---|
| grammar error score, before -> after | 555 -> 127 | 555 -> 135 |
| share of measurable errors removed | 77.1% | 75.7% |
| meaning changes reported | 96 | 85 |
| protected tokens dropped | 0 | 0 |
| essays where the error score rose | 0 | 0 |
| memory the policy asks for | 8 GB | 16 GB |

## What this does and does not show

It does not show that E2B corrects better. A 1.4 point gap over twenty
essays is inside the noise of a sample this size, and the two packs disagree
in opposite directions on the two headline numbers: E2B removes slightly
more grammar errors, E4B changes slightly less meaning. Either could lead on
a different twenty essays.

What it does show is that **E4B buys no quality worth its cost here**. It
asks twice the memory, takes longer per paragraph, and lands in the same
place. On the strength of this corpus there is no reason to reach for it.

## Left alone deliberately

`_MODE_PACK_ORDER` still prefers E4B for Auto and Balanced. Changing that
would have meant rewriting nine tests that encode the current intent, on
evidence that does not support the claim. The measurement is recorded here
so the decision can be made on purpose rather than as a side effect.

If it is changed, the argument is memory and speed on small machines, not
correction quality - and E2B should then be re-measured on a second corpus
first, because this one is a single sample of deliberately error-heavy text.
