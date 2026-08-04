# Correction Reliability Repair - 2026-08-01

Continues `CORRECTION_GATE_RELIABILITY_2026-07-31.md`. That pass fixed the
false-rejecting gates in a sandbox. This pass ran the real GPU pipeline
(gemma4:12b on CUDA via Ollama) end to end, measured the remaining failures,
and fixed each root cause. All changes are platform-neutral text logic or
standard Ollama request options; nothing depends on this machine's GPU,
model, or install location.

## New probe

`tools/correction_reliability_probe.py` runs 8 sentence cases and 4
Writing-HUD-sized section cases through the real `PolishEngine` N times and
scores three things per case: consistency (one distinct output), always-AI
(no silent rules-only fallback), and accuracy (the seeded error is fixed and
protected values survive). Report: `build/correction-reliability-*.json`.

Baseline before fixes: sections 0/4 accurate, one flip-flopping output, one
case with AI silently skipped. After fixes: 12/12 on every axis, and two
separate processes produce byte-identical outputs.

## Root causes found and fixed

1. **Non-deterministic sampling** (`selected_text.py::_generate`,
   `correction.py`). Options sent only `temperature: 0`. With Ollama's
   default top-k/top-p sampler and unpinned seed, near-tied logits flip
   between runs; the same text visibly alternated between two corrections.
   Now greedy and pinned: `seed: 1, top_k: 1, top_p: 1.0,
   repeat_penalty: 1.0`.

2. **Misplaced-apostrophe contractions** (`polish.py`). "was'nt" was not in
   `_CONTRACTION_NORMALIZE`, so the perfect repair "was'nt" -> "was not"
   raised the candidate's negation count and the safety validator rejected
   the whole correction as "negation changed". All misplaced forms
   ("do'nt", "was'nt", "could'nt", ...) now normalize like the
   missing-apostrophe forms.

3. **Written-number facts quoted a broken noun back to the model**
   (`selected_text.py`). The prompt's WRITTEN-NUMBER FACTS line captured
   "two day" verbatim and marked it immutable, so the model obediently kept
   the grammar error. `_repair_time_unit_plural` now fixes the singular
   time unit inside the phrase ("two day" -> "two days"; "one day"
   unchanged), and `_missing_number_fact_phrases` accepts singular/plural
   variants so the repair is not counted as a lost fact.

4. **Digit tokens and service names** (`polish.py`
   `_generation_instruction`). The model reformatted "3pm" -> "3 p.m."
   (tripping the protected-token and identifier gates, discarding the whole
   correction) and replaced "gmail" with "email". The instruction now
   requires copying every digit-bearing word exactly and forbids swapping a
   product/app/service name for a generic word.

5. **Transient probe failures were cached forever**
   (`polish_hardware.py::PolishBenchmarkCache`). A failed acceptance probe
   (typically: another local app held the GPU mid-generation, so the model
   could not reach full residency) was stored with `accepted=False` under a
   key with no time component. Every later correction silently planned
   rules-only. Failed records now expire after
   `FAILED_RECORD_TTL_SECONDS` (5 minutes, matching the runtime
   keep-alive); passing records still persist. This is the portable fix for
   the "Plan B hardware determinism" gap: low-VRAM or busy systems recover
   instead of degrading permanently.

## Verification (this machine, real GPU)

- `tools/correction_reliability_probe.py --repeats 3 --scope all`:
  12/12 consistent, 12/12 always-AI, 12/12 accurate; two independent
  processes byte-identical (`build/correction-reliability-final-a/b.json`).
- Five-page full-page gate `tools/gpt56sol_five_page_probe.py
  --model gemma4:12b --modes Proofread`: 5/5
  (`build/gpt56sol-proofread-gemma-recheck.json`), matching the best prior
  baseline `evidence/gpt56sol-five-page-gemma4-proofread-r4.json`.
- Focused suites: 666+45 passing (`test_polish_engine` 141, `test_polish`,
  `test_polish_quality_repair`, `test_selected_text`, `test_correction`,
  `test_polish_classifier`, `test_writing_suggestions`,
  `test_polish_gate_redesign` incl. new expiry/plural/apostrophe locks).
- Two engine tests updated: their cached-failure fixtures used hardcoded
  old timestamps that the new expiry correctly voids; they now use fresh
  timestamps, preserving the original intent (a live cached failure).

## Notes for other systems

- CPU-only machines: unchanged policy path; the sampler pin and gate fixes
  apply identically.
- The 5-minute failed-record TTL means a machine whose GPU is busy retries
  the probe at most every 5 minutes instead of never.
- Known pre-existing leaks unchanged: clock-time value changes
  ("3pm" -> "5pm") and "The <Name>" proper-noun removal are still not
  caught by the validator (documented 2026-07-31).
- Android's `DocumentCorrectionPipeline.kt` has its own gate logic; these
  fixes are Windows-core only and should be ported in a separate pass.

## Status

Changes are uncommitted on `codex/manuscript-gemma-rebuild`, alongside the
2026-07-31 gate fixes. Product remains sale-blocked; route any release
build through the Board vote.
