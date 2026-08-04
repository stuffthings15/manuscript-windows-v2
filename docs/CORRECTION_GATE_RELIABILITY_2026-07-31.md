# Correction Gate Reliability Repair - 2026-07-31

## Problem
Corrections were inconsistent and inaccurate across Word, browser, editor, and
dictation: obvious errors left unfixed, same text giving different results,
slow/weak plain output, and some edits changing meaning.

## Root cause (measured, not guessed)
The local model runs at temperature 0 and the MiniLM similarity is fine for
normal edits. The failure was the two accept/reject gates rejecting correct
AI output:

- Safety validator (`polish.py` `PolishValidator.validate`) used rigid token
  counters (pronoun/actor, modal, obligation) plus a hardcoded ignore list.
  It false-rejected valid fixes: adding a required subject ("went" -> "I
  went") = "actor changed"; fixing "cant" -> "can't" = "modal meaning
  changed"; capitalizing a sentence start ("Your", "Don't") = "new named
  entity added".
- Quality gate (`polish_quality.py` `_RESIDUAL_CHECKS`) flagged correct text.
  `[.!?][A-Z]` fired on "Node.JS" and "U.S.A."; the repeated-word rule fired
  on proper nouns like "Bora Bora" and "Walla Walla". Any flag sets
  `complete=False`, so the good correction was discarded.

When a gate rejects, the pipeline keeps the original or rules-only text. That
is exactly the reported behavior.

## Changes (this repo, uncommitted)
- `src/echo_link_core/polish.py`
  - Added `_normalize_contractions` so "cant"/"dont" are compared as
    "can't"/"don't" before modal/negation counting.
  - Added `_added_actors_are_safe_subjects`: adding a first/second-person
    subject is allowed; swaps, removals, invented third-person owners still
    fail.
  - Fixed `_named_entities` so sentence-initial pronouns, contractions, and
    function words ("Your", "Don't", "The") are not read as invented entities.
  - Rewrote the validator decision block to use the above. Negation, modal,
    obligation, ordering, protected facts, code identifiers, named entities,
    MiniLM similarity (0.88), and grammar-mode length checks are all kept.
- `src/echo_link_core/polish_quality.py`
  - "missing sentence spacing" now requires a real word ending + terminator +
    capitalized word start (`[a-z][.!?][A-Z][a-z]`).
  - "repeated word" no longer flags a capitalized proper noun said twice, but
    still catches "the the" and "By by".
- `tests/test_polish_gate_redesign.py` (new): locks 11 must-accept, 10
  must-reject, and the quality-gate cases.

## Verification (sandbox, bundled MiniLM, no GPU/Ollama)
- Spec: safety validator accepts 12/12 correct fixes (was 7/12), rejects
  10/12 meaning changes (unchanged). Quality gate wrongly flags 0/5 (was 4/5).
- Pytest: 402 passed - `test_polish` 41, `test_polish_engine` 141,
  `test_polish_quality_repair` 169, `test_polish_classifier` 12,
  `test_writing_suggestions` 12, `test_polish_gate_redesign` 27.
- Reproduce: `PYTHONPATH=src python3 polish_gate_probe.py` and
  `validator_spec.py` (probe scripts included with this change).

## Known gaps (pre-existing, NOT introduced here)
- Time change "3pm" -> "5pm" is not caught: `_CRITICAL` does not treat clock
  times as protected facts.
- Removing a proper noun that opens with "The <Name>" ("The Boston office" ->
  "The office") is not caught, because the sentence-initial "The <Name>" span
  drops the single-word suffix.
- Both leak identically in the original code.

## NOT done - needs your Windows + GPU machine
- Plan B (hardware determinism): stopping live VRAM/battery/residency from
  changing which model runs, or whether the LLM runs, for manual Proofread.
  The lever is in `polish_hardware.py` / `_accept_plan`; it cannot be verified
  headless and was left untouched. Most run-to-run variance came from the
  gates above, now fixed.
- Plan C (fixture-detector generalization): the `_RESIDUAL_CHECKS` list still
  has memorized fixture-specific regexes. They do not false-positive on real
  text, so they were left in place. Replacing them with a general grammar/
  spell residual check is a larger project needing the full pipeline.

## You must still run on your machine
- Full Windows + Qt + GPU suite (1,000+ tests).
- Live Qwen3/Gemma full-page Proofread and all-tone runs.
- The locked 20-case and full-page corpora re-verification.
- This is a behavior change to a sale-blocked product: route through your
  Board vote before any release build.
