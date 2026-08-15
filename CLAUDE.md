# Working in this repository

## Read the AI Brain first. Before anything else.

`C:\Users\stuff\Documents\AI Brain` is the authoritative cross-session record.
It survives account switches, session switches, and the Claude Code session
registry getting wiped by an app update. Read it in this order before touching
this project:

1. `AI Brain/instructions.md`
2. `AI Brain/context.md`
3. `AI Brain/memory.md`
4. `AI Brain/Echo-Link/context.md` and `AI Brain/Echo-Link/memory.md`

You should not have to read those files by hand: hooks load them at session
start, at every compaction, and in short form on every turn.

**If no AI Brain block arrived before your first reply, the hooks are gone.**
That is the one failure that silently restores the old behaviour, because a
missing safety net looks exactly like a session that read everything. Check it
and say so before doing any work:

```bash
python "C:/Users/stuff/Documents/AI Brain/tools/brain_context.py" --doctor
```

Claude Code reports this session's working directory as `Documents`, the parent
of every project, so path matching alone cannot tell which project you are in.
If the reminder says NO PROJECT MEMORY IS LOADED, clear it once with
`--set-project Echo-Link --session <id>`; the id is in the reminder.

At session end, append one dated bullet to `AI Brain/Echo-Link/memory.md`
saying what was decided, what was done, and what is still open. Append-only,
no approval needed.

This is not optional and it is not a formality. On 2026-08-14 a session skipped
it, rebuilt a measurement path that already existed, ran the corpus headless
past a guard built to prevent exactly that, and reported a false product
regression to Curtis. Three weeks of "constant regression" is mostly this: the
record exists and the session did not read it.

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

Baseline is 2145 passed, 2 skipped, 9 to 25 minutes. A drop is a regression, not
a cleanup, unless it is an intentional known defect recorded in
`docs/ARCHITECTURE.md`.

```bash
.venv/Scripts/python.exe -m ruff check src tests
```

```bash
.venv/Scripts/python.exe run_manuscript.py
```

Correction quality is measured through Word and the HUD, one essay per Word
session, accepting each section the way a writer does. Ask first: it takes the
screen for up to 45 minutes.

```bash
.venv/Scripts/python.exe tools/word_essay_session.py --essay 1 --first-wait 200 --section-wait 150
```

```bash
.venv/Scripts/python.exe tools/run_essays_batch.py --from 1 --to 20
```

Both take `--corpus` and both glob `*.docx`, so a corpus that arrives as plain
text needs converting before the harness can see it.

## Non-negotiables

These are not preferences. Breaking one has already cost a session's work.

- **Never destroy files.** Never `git checkout --` a file to undo something;
  it discards uncommitted work irrecoverably. Revert precisely with edits, or
  recover a specific version with `git show <rev>:<path>`.
- **Stage with `git add -p`, never whole files.** One commit per verified
  change, with its regression test.
- **`test-corpus/animal-pet-20` is integrity locked.** Read from it, copy essays
  out, never write into it.
- **Never measure correction quality headlessly.** Going straight at
  `PolishEngine` skips `tools/word_essay_session.py`, which refuses to start
  when the local AI has fallen back to deterministic rules, because a
  rules-only essay comes back thin and unchanged and reads exactly like a
  broken corrector. On 2026-08-14 a headless run scored a corpus at a mean of
  62 and it was reported to the user as a product regression; the AI had
  latched off after two truncated answers and the real harness would have
  refused the run. A headless harness is a debugging aid. It is never the
  number.
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

`_apply_fast_proofread_rules`, now in `polish_proofread.py`, accumulated 149
memorized sentences from the benchmark fixtures out of 512 rules: strings like
`RB-SYNC-64` and `gardens raise rent`, each existing only in that table and the
test asserting it. Those tests were green and proved nothing, and a new essay
with the same grammar defect got no correction because the fix was pinned to one
sentence.

These are **accepted debt, not a backlog to grind**. The decision was taken on
2026-08-12 after four clusters were attempted: two generalized and replaced 13
rules, and the rest could not be generalized here at all. Telling "customer
names gate codes and service notes" from a noun phrase needs part-of-speech
knowledge this layer does not have, "must not be presented them as" is shaped
exactly like the correct "she was shown this as an example", and a rule for
"did not measure analyzed" destroyed "we can use measured data" when tried.

Do not delete them in bulk either: the rules chain, so one can fire on an
intermediate string that appears in no test, and removing 85 that looked
unasserted failed 309 tests.

`tests/test_source_discipline.py` freezes the count at 149. The table can
shrink, never grow. If you replace some, lower the ceiling in the same commit.

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
