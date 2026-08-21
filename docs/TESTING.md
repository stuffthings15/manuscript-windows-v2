# Measuring this product

How to take a number that means something, and how to tell when one does not.

Everything here was learned by taking numbers that turned out to be worthless.
Each rule names what it cost.

## The one rule

**Correction quality is measured through Word and the HUD, as a writer.** Never
headlessly. On 2026-08-14 a headless run scored the corpus at a mean of 62 and
it was reported as a product regression; the AI had latched off after two
truncated answers, and the real harness would have refused to start.

A headless harness is a debugging aid. It is never the number.

## Before you trust any number

Four checks. Any one of them failing means the number is not a measurement.

### 1. `applied=N`, and N is not zero

```
run1 463.4s complete presses=25 ... tone=Formal applied=25
```

`applied=0` is **the absence of a measurement, not a score of zero.** On
2026-08-20 three tones were judged after `applied=0` runs and reported 0.0
percent each. Nothing had been measured at all. The judge now refuses, but
check the row yourself before quoting a percentage.

### 2. The running process is the build under test

Three separate times in two days a measurement ran against a process that was
not the build being tested, and one of those cost six hours: `restart_app_and_wait`
killed `pythonw.exe` by image name while the launcher had fallen back to
`python.exe`, so every "restart" reported success and changed nothing.

```bash
.venv/Scripts/python.exe tools/restart_app_and_wait.py     # must say loaded=True
```

Then confirm it independently - the state file publishes the build:

```bash
python -c "import json,pathlib;print(json.loads((pathlib.Path.home()/'AppData/Local/ManuscriptWindowsV2/hud-state.json').read_text())['build'])"
```

It must match `git rev-parse --short=12 HEAD`.

### 3. The feature under test actually fired

A commit message is not evidence. On 2026-08-21 a commit claimed tone requests
now spanned 4 to 5 paragraphs; the product was still sending 43 characters,
because the path a writer actually uses had never been wired. One grep of the
live log would have caught it before the commit.

```bash
grep -oE "command=Friendly characters=[0-9]+" ~/AppData/Local/ManuscriptWindowsV2/echo-link.log | tail
```

A batched tone request is around 800-2500 characters. A 43-character one means
the run measured the wrong thing.

### 4. The document actually changed

`shutil.copy2` preserves the source's timestamp, so the working copy in
`build/batch/` legitimately carries the SOURCE's mtime. **A stale-looking date
is not evidence of anything**, and reading it as such wasted an investigation
on 2026-08-21. Compare content, never mtime.

## Reading the live state

The HUD publishes what it is doing to
`%LOCALAPPDATA%/ManuscriptWindowsV2/hud-state.json`, rewritten on every change,
and appends transitions to `hud-events.jsonl`.

```bash
python -c "
import json,pathlib
d=json.loads((pathlib.Path.home()/'AppData/Local/ManuscriptWindowsV2/hud-state.json').read_text())
print(f\"{d['stamp'][11:19]} sec={d['section_index']}/{d['section_total']} ready={d['sections_ready']} replace={d['replace_enabled']} tone={d['tone_selected']} view={d['view']}\")"
```

This exists because the harness used to determine state by string-matching an
accessibility label, and anything the label did not literally say became
`unknown`. A failing run reported `unknown=54`: fifty-four looks at the card
with no idea what it was seeing.

Reading the state file once immediately disproved three separate hypotheses
about a stuck run and located the real one. Prefer it to any inference from
button states, and prefer it to a HUD dump.

## Running a tone measurement

Ask first if Curtis is at the machine. A sweep holds the GPU for about ten
minutes per tone, and on 2026-08-21 the 27B judge held 14.3 GB of 16.4 GB while
dictation preload went from 7 seconds to 112.

```bash
.venv/Scripts/python.exe tools/restart_app_and_wait.py
.venv/Scripts/python.exe tools/run_essays_batch.py --from 1 --to 1 \
    --corpus test-corpus/error-riddled-20-docx --tone Friendly --as-a-writer
.venv/Scripts/python.exe tools/tone_correctness.py \
    --source test-corpus/error-riddled-20-docx/01_urban_beekeeping_and_the_ecology_of_cities.docx \
    --result build/batch/01_urban_beekeeping_and_the_ecology_of_cities.docx \
    --tone Friendly --model gemma3:27b
```

Restart between tones. Judge with `gemma3:27b`; the default judge model is not
installed here and a missing model returns 404, which an earlier gate turned
into a reported 0.0 percent.

`MANUSCRIPT_TONE_BATCH=0` returns the tone path to single-section requests. It
exists so "is batching the cause" is a one-run experiment instead of an
argument, and it answered that question on 2026-08-21: it is not.

## Failure signatures seen so far

| Signature | What it means |
|---|---|
| `applied=0 unknown=54 presses=0` | The harness could not classify the card and pressed nothing. Not a product result. Read `hud-state.json`. |
| `applied=0`, `ran={}`, 13 second run | The harness attached to a HUD that was not ready. Restart and retry. |
| Judge reports `section counts differ` | Source and result paragraph counts disagree. Never filter blocks before pairing the two sides. |
| Every tone 0.0 percent | Historically a judge failure, not a product failure. Check the judge model is installed. |
| `characters=43` on a tone | Batching did not fire. The run measured the unbatched path. |

## Stopping a run

`TaskStop` and killing the wrapper is **not enough**. On 2026-08-21 a stopped
sweep kept running as a bash script with Python children, and a "clean"
verification ran against a contested app - that result was invalid and
discarded. Confirm and kill by hand:

```bash
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { \$_.CommandLine -like '*run_essays_batch*' } | ForEach-Object { \$_.ProcessId }"
```

Beware: that query's own command line contains the search string, so it matches
itself. Check the executable before killing anything.

## The suite

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src tests tools
```

Baseline at the time of writing: **2387 passed, 2 skipped**, 12 to 20 minutes.

`--timeout` is NOT installed here, and pytest **exits 0 after running zero
tests** when it rejects that argument. Judge the run by its "N passed" line,
never by the exit code. A green exit from a run that collected nothing looks
identical to a passing suite in a log tail.

There is a known shutdown crash (STATUS_STACK_BUFFER_OVERRUN) that can make the
suite exit non-zero with zero failures. It predates this work and does not gate.
