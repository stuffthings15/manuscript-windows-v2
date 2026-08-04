# Manuscript Full-Page Tone Quality Audit

Date: 2026-07-22

## Verdict

Manuscript passed the final locked full-page quality audit for every visible tone.

- Normal full-page quality: **12 of 12 passed**.
- Protected-fact stress quality: **12 of 12 passed**.
- Complete application suite: **860 passed, 0 failed, 2 environment/evidence skips**.
- Website lint: **0 errors and 0 warnings**.
- Website production build and tests: **2 passed, 0 failed**.

The initial baseline was 1 of 12 normal tones and 0 of 12 protected tones. The correction loop continued until both final tone runs passed 12 of 12.

## Test design

The locked synthetic source is 467 words in six paragraphs. It intentionally contains:

- 14 detectable spelling defects
- 11 detectable grammar defects
- 9 detectable punctuation defects
- 6 detectable formatting defects

Every visible HUD tone was exercised: Clear, Improve, Rephrase, Shorten, Friendly, Formal, Professional, Diplomatic, Empathetic, Cooperative, Assertive, and Caveman. Clear routes to Rewrite.

The probe uses the real local `LocalWritingAssistant` path and the exact HUD workflow of one-to-three-sentence chunks. Every tone uses `qwen2.5:14b`. A separate local `gemma3:27b` judge evaluates only target style and natural usefulness. Deterministic code separately checks spelling, grammar, punctuation, formatting, required facts, written numbers, actors, ownership, timing, privacy boundaries, paragraph structure, and line breaks.

A tone passes only when:

- target style and usefulness both score at least 4 of 5
- no known source surface defect remains
- no required term, number, fact, owner, or relationship is lost or invented
- all six paragraphs and the original line-break structure remain
- Shorten produces real compression
- protected stress keeps every protected phrase exact

The source SHA-256 is `a9733c60bf7d44e6a5f4f43381c5c4dad3fd20e5cf361941911ce741dfd098f7` in both final runs.

## Final normal results

| Tone | Result |
|---|---:|
| Clear | Pass |
| Improve | Pass |
| Rephrase | Pass |
| Shorten | Pass |
| Friendly | Pass |
| Formal | Pass |
| Professional | Pass |
| Diplomatic | Pass |
| Empathetic | Pass |
| Cooperative | Pass |
| Assertive | Pass |
| Caveman | Pass |

Final result: **12 of 12 passed**.

## Final protected-fact results

The protected run makes important product phrases immutable. It also checks exact occurrence counts and order while still requiring a correct, natural rewrite.

| Tone | Result |
|---|---:|
| Clear | Pass |
| Improve | Pass |
| Rephrase | Pass |
| Shorten | Pass |
| Friendly | Pass |
| Formal | Pass |
| Professional | Pass |
| Diplomatic | Pass |
| Empathetic | Pass |
| Cooperative | Pass |
| Assertive | Pass |
| Caveman | Pass |

Final result: **12 of 12 passed**.

## Corrections verified by the loop

The loop added and verified safeguards for:

- launch readiness versus a future plan
- activation sound timing
- report ownership and the three users who wrote several pages
- accepted spelling fixes that did not change text or move the HUD
- exact names, quantities, retention periods, and glossary terms
- local-operation and private-report boundaries
- reviewer ownership and obligation strength
- clipboard restoration after an approved fallback
- protected tokens beside non-ASCII model output
- smart-apostrophe contractions
- deterministic protected-token ordering and targeted retries
- short, blunt, correct Caveman output without caricature grammar

## Automated suite results

The final application run completed with **860 passed and 0 failed**. Two tests were skipped for explicit reasons:

- symbolic links are unavailable on this machine
- live external-insertion acceptance evidence has not been recorded

These are not hidden failures. They remain separate from the 860 runnable tests that passed.

The website also passed lint, production compilation, and both rendered/security tests. The build printed one Vinext route-classification notice; it was not a failure and did not affect the build or tests.

## Evidence

- `evidence/full-page-tone-quality.json` — final normal run with full rewritten pages and per-tone scores
- `evidence/full-page-tone-quality-protected.json` — final protected-fact run
- `tools/full_page_tone_quality_probe.py` — reproducible local probe
- `tests/test_full_page_tone_quality_probe.py` — corpus, scoring, relationship, and checker regressions

## Limits

This is strong local evidence for the locked regression page, not proof that every possible document will always be rewritten perfectly. The style judge is a local language model. Release review should keep human sampling across additional document types. The skipped live external-insertion acceptance check still needs real acceptance evidence before claiming that external workflow as verified.
