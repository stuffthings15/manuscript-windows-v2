# autoresearch, Manuscript

An experiment to have the agent improve Manuscript's correction quality on its
own, overnight, keeping only what it can prove.

This follows https://github.com/karpathy/autoresearch (HEAD `228791f`, mirrored
read-only at `Vedit-AI/third_party/autoresearch_ref`). Where this file is silent,
the upstream `program.md` is the rule.

**This file is Curtis's.** The human iterates on it; the agent does not edit it.
It is the place to change how the loop behaves, rather than editing the loop's
machinery.

## Setup

To set up a new run, work with Curtis to:

1. **Agree a run tag** based on today's date (e.g. `aug28`). The branch
   `autoresearch/<tag>` must not already exist.
2. **Create the branch**: `git checkout -b autoresearch/<tag>` from master.
3. **Read the in-scope files**: `CLAUDE.md`, `docs/ARCHITECTURE.md`,
   `docs/RELEASE_ACCEPTANCE.md`, `tools/autoresearch_run.py`, and the polish
   modules you intend to touch. Also `AI Brain/Echo-Link/memory.md`, which
   records what has already been tried and why it was rejected.
4. **Verify the environment**, before committing to a night of runs:
   - `ollama list` shows the expected pack
   - the app launches: `.venv/Scripts/python.exe tools/restart_app_and_wait.py`
   - Word is healthy and no stale `WINWORD` or `pythonw` processes are running
   - the corpus exists: `test-corpus/error-riddled-20-docx`
   - a single experiment completes and prints a `score:` line
5. **Initialise `results.tsv`** with only the header row.
6. **Get the overnight waiver** from Curtis naming the branch and the window,
   then go.

## The experiment

One experiment is a fixed slice of essays corrected through Word and the HUD,
then graded. Launch it as:

```
.venv/Scripts/python.exe tools/autoresearch_run.py --essays 8,10 --require-idle 300 > run.log 2>&1
```

`--essays 8,10` because those are the two weakest essays and therefore the only
ones with headroom. Essay 1 scores 100 out of 100, and a night spent optimising
it would measure every change as zero.

`--require-idle 300` because this runs whenever the machine is free rather than
only overnight. If anyone has touched the keyboard in the last five minutes the
run exits 2, prints `skipped:` and measures nothing. Treat exit 2 as "come back
later": do NOT write it to results.tsv, because nothing was measured and a
skipped tick is not a crash.

That gate is not politeness. An experiment kills Word before every essay, and
typing in any other editable window redirects the HUD and crashes the run. Both
happened on 2026-08-28.

**Redirect it. Do not use tee and do not let the output into your context.** A
batch prints thousands of lines and you have a hundred experiments to run.

Read the result with exactly one grep:

```
grep "^score:\|^crash:" run.log
```

### What you CAN do

- Modify `src/echo_link_core/polish*.py`. The correction cascade, the prompts,
  the generation parameters, the deterministic rules, the hardware plan.

### What you CANNOT do

- Modify the evaluator: `tools/grade_corpus.py`, `tools/tone_correctness.py`,
  `tools/autoresearch_run.py`. These are ground truth, the equivalent of
  upstream's read-only `prepare.py`.
- Modify anything under `test-corpus/`. `animal-pet-20` is integrity locked.
- Move a threshold in `docs/RELEASE_ACCEPTANCE.md`. Curtis moves those, nobody
  else. A threshold is what shipping means.
- Install packages or add dependencies.
- Edit the meaning guards in `polish.py`. They were examined one by one and five
  of six apparent false refusals turned out to be correct.
- Grow `_apply_fast_proofread_rules`. `tests/test_source_discipline.py` freezes
  it at 149 and it may shrink, never grow.
- Touch `CLAUDE.md`. Upstream generates it per session and ignores it; here it is
  the committed rules file, and this file replaces it as your prompt.
- Run experiments in parallel. One Word instance, one screen, one at a time.

### The one cheat that would work, and must not be taken

The metric is a corpus score, and the most effective way to raise it is to
memorise the corpus: hard-code the essays' sentences, spellings and phrasings
into the rules. This codebase has already done exactly that once - 149 benchmark
sentences pinned into production rules, every test green, and a new essay with
the same defect got no correction because the fix was pinned to one sentence.

If you find yourself writing a regex that matches one specific sentence, or
adding a word because a corpus essay contains it, **stop**. You are recording
the answer, not fixing the defect. The general rule that failed to fire is the
fix.

The test that catches this is `tests/test_source_discipline.py`, and it must
pass at every kept commit.

## The metric

**Get the highest score.** One number, from `tools/grade_corpus.py`: spelling,
homophones, punctuation, grammar and coherence, scaled to 100. Higher is better.

The six release gates are not the metric. They decide whether a branch may merge,
and Curtis approves that separately. Do not optimise against them.

**VRAM** is a soft constraint. Some increase is fine for a real gain; it should
not blow up.

**Simplicity criterion.** All else equal, simpler is better. A small gain that
adds ugly complexity is not worth keeping. An improvement that comes from
deleting code is definitely kept. Roughly no change but much simpler code is also
a keep - and shrinking `_apply_fast_proofread_rules` is exactly the direction
that table is allowed to move.

**The first run establishes the baseline.** Run it unchanged.

**The spread has been measured and it is ZERO.** Three unchanged runs on essays
8 and 10 scored 91.500000 every time, with identical press counts, on
2026-08-28. The corrector decodes greedily, so identical inputs give
byte-identical corrections.

That makes the keep rule simple: any improvement is real, because the
measurement does not wander. Upstream needs a threshold; this does not. What
still varies is the CRASH RATE, so treat a crash as no information rather than
as a bad result, and re-run it.

## Output format

A finished experiment prints:

```
---
score:            96.660000
essays:           1
lowest:           96.660000
elapsed_seconds:  311.0
pack:             gemma4-12b-qat-v1
model:            gemma4:12b-it-qat
backend:          cuda
build:            78d0883
peak_vram_mb:     10240.0
unknown:          1
```

If there is no `score:` line there is a `crash:` line instead, and the run
produced no measurement. That includes a stale app: the runner restarts the app
before every experiment and then checks that the build it published matches this
checkout, because an experiment measured against the previously loaded code
returns the baseline plus noise and looks entirely reasonable.

`pack`, `model` and `build` say what actually served the run. Eight acceptance
tiers were once measured on a 2 billion parameter pack while reporting a larger
one, so a score without these is not a measurement.

## Logging results

Log every experiment to `results.tsv`, tab separated, NOT comma separated.

```
commit	score	memory_gb	status	description
```

1. short git commit hash
2. score achieved, `0.000000` for crashes
3. peak memory in GB to one decimal, `0.0` for crashes
4. status: `keep`, `discard`, or `crash`
5. a short description of what the experiment tried

Do not commit `results.tsv`. It is gitignored.

## The experiment loop

ONE TURN IS ONE EXPERIMENT. Everything after the code edit is a single command:

```
.venv/Scripts/python.exe tools/autoresearch_cycle.py --describe "what this tries"
```

It performs steps 3 and 5 through 9 of the upstream loop - commit, run, read one
line, record in results.tsv, and keep or `git reset --hard` - and prints exactly
one line: `KEEP 94.5`, `DISCARD 94.0`, `CRASH dead`.

So a turn is: change `src/echo_link_core/polish*.py` with an idea, run the
cycle, read the line, schedule the next. Two tool calls.

This matters more than it looks. The loop used to be edit, twelve-minute test
suite, fix, twelve-minute suite, commit, twelve-minute Word run, report - and
every one of those was a turn where a human had to say "keep going". An
autonomous loop that needs a prompt between its phases is not autonomous.

DO NOT run the test suite between experiments. The specification's loop contains
no tests, and the suite gates the merge to master. `ruff` runs inside the cycle
because seconds are affordable and a syntax error wastes an experiment.

**Crash shapes specific to Manuscript**, all of which mean no score rather than
a low one:

- `dead` - the corrector never returned a review
- `partial` - the essay stopped part way. Scoring these is how 11 of 20 runs were
  once averaged into a mean of 84.5 and reported as a regression that had not
  happened
- `captured from 'Claude'` - the harness captured the wrong window
- `COMError (-2146233083)` - Word fell over

**Timeout**: an experiment is about 5 minutes per essay. Past 10 minutes per
essay, kill it and treat it as a failure.

**Every kept commit must pass** `tests/test_source_discipline.py`. Run the full
suite before proposing a merge, not after every experiment.

**NEVER STOP.** Inside the waiver window, do not ask whether to continue. Curtis
is asleep. If you run out of ideas, think harder: re-read the in-scope files,
read `AI Brain/Echo-Link/memory.md` for what has already failed and why,
combine previous near-misses, try more radical changes. The loop runs until
interrupted.

Rewinding the branch is allowed if you are stuck, but very sparingly, if ever.

## In the morning

Leave Curtis: `results.tsv`, the output of `tools/autoresearch_analysis.py`, and
one dated bullet in `AI Brain/Echo-Link/memory.md` saying what was tried, what
was kept, and what is still open.

Nothing merges to master without all six release gates and Curtis's sign-off.
