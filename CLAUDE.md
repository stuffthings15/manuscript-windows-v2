# Working in this repository

Manuscript is a local-only Windows voice dictation and writing assistant. No
cloud runtime, no telemetry, no network calls in the correction path. Every
engineering decision below follows from that: the user's text never leaves the
machine, so correctness and privacy outrank cleverness and speed.

Read `docs/ARCHITECTURE.md` before changing anything you have not touched
before. This file is the rules; that file is the map.

## Commands

Run the full suite before every commit. It is the safety net that makes
refactoring survivable.

```bash
.venv/Scripts/python.exe -m pytest -q
```

Baseline is 2025 passed, 2 skipped, 9 to 17 minutes. A drop is a regression, not
a cleanup, unless it is an intentional known defect recorded in
`docs/ARCHITECTURE.md`.

```bash
.venv/Scripts/python.exe -m ruff check src tests
```

```bash
.venv/Scripts/python.exe run_manuscript.py
```

## Non-negotiables

These are not preferences. Breaking one has already cost a session's work.

- **Never destroy files.** Never `git checkout --` a file to undo something;
  it discards uncommitted work irrecoverably. Revert precisely with edits, or
  recover a specific version with `git show <rev>:<path>`.
- **Stage with `git add -p`, never whole files.** One commit per verified
  change, with its regression test.
- **`test-corpus/animal-pet-20` is integrity locked.** Read from it, copy essays
  out, never write into it.
- **Never weaken a safety property or a test to make something pass.** A failing
  test is an implementation defect, an incorrect test, or a changed requirement.
  Work out which before touching anything.
- **Do not modify the meaning guards** without being asked to. They were
  examined one by one; five of six apparent false refusals turned out correct.
  They may be moved between modules unchanged, never edited in passing.
- **No em dashes** in code, comments, docs, commit messages, or anything written
  for the user. ASCII hyphens only.
- **Ask before running long automated Word sessions.** They steal focus and take
  the screen for up to 45 minutes.

## The rule that this codebase actually broke

Never hard-code test-specific values into production code.

`_apply_fast_proofread_rules` accumulated roughly 30 memorized sentences from
the test corpus, strings like `RB-SYNC-64` and `gardens raise rent`, each
existing only in that function and the test asserting it. Those tests were green
and proved nothing, and a new essay with the same grammar defect got no
correction because the fix was pinned to one sentence.

When a corpus essay corrects badly, the fix is the general rule that failed to
fire. If you find yourself writing a regex that matches one specific sentence,
stop: you are recording the answer, not fixing the defect.

## Before writing new code

Search first. Prefer reuse, then extend, then refactor, then create new.

This repository already knows how to do more than it looks like. Specifically:

- Spelling corrections have exactly one source of truth in
  `polish_spelling.py`. Do not add a second map.
- Deterministic, no-I/O suggestions live in `writing_suggestions.py`. It must
  not import the dictionary or warm the speller.
- Hardware decisions (context window, output tokens, GPU requirement) come from
  the `ExecutionPlan` (defined in `polish_types.py`, built in
  `polish_hardware.py`). Do not pass competing
  constants from the app layer; they are discarded and they mislead the next
  reader.
- New correction modules join the `polish_*` family in `echo_link_core`. Do not
  invent a parallel structure for one feature.

## Error handling

Broad `except Exception` requires a written reason at the call site. COM and UI
Automation genuinely throw undocumented types, which is a legitimate reason, but
it has to be visible.

Never swallow silently. An error is handled, translated into a domain error,
logged with enough context to diagnose it, or propagated. Never log the user's
text, credentials, or file contents.

## Tests

Test behaviour, not structure. A test that asserts the shape of a call rather
than its effect will pass while the feature is broken; one did.

Every bug fix ships with a regression test, and **you must observe that test
failing without the fix**. Two dictation back-off tests were green with the fix
removed and proved nothing until this was checked. Disabling the fix and
watching the test fail costs a minute and is the only thing separating a
regression test from decoration.

## Comments

Explain why, not what. The valuable comments in this codebase record what was
measured: which approach was tried, what it broke, why the current shape exists.
Preserve those when refactoring; they are the reason the same bug is not
rediscovered.

## Before claiming anything works

State evidence or state that it is unverified. Run the suite, run ruff, review
the diff as a hostile reviewer, and exercise the real workflow when the change
touches one. "Fixed", "complete", and "production ready" require proof.
