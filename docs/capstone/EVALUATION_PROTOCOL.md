# Echo-Link Capstone Evaluation Protocol

> The consumer Echo-Link app no longer exposes a Capstone tab or navigation link. The corpus, records, metrics, and export engine remain for separate internal evaluation work. Do not restore the consumer tab to run this protocol; use a dedicated internal evaluation surface if the human study resumes.

## Purpose

Prove whether Echo-Link reduces the work needed to turn speech into accepted text. Do not claim improvement from automated cleanup speed alone.

## Fixed test set

- Use `gold_corpus.json` without changing its raw or expected text after baseline.
- Twenty cases cover prose, questions, punctuation, paragraphs, repetition, fillers, self-correction, lists, names, numbers, email, URL, path, filename, code, and GPT workflow wording.
- Add new cases only as a new corpus version. Never rewrite a failed baseline case to make the score rise.

## Metrics

### Primary metric 1: correction-free acceptance rate

`accepted without any text edit / completed cases`

- Count a case only after the user sees the result.
- Copy or insertion without editing counts as accepted.
- Punctuation, capitalization, word, or formatting edits make the case edited.
- Rejected, retried, or failed cases do not count as correction-free.

### Primary metric 2: median time to accepted text

- Start when the user begins the dictation attempt.
- Stop when the user accepts, copies, or inserts the final usable text.
- Include retry and manual edit time.
- Report the median across all completed cases.

### Supporting automated metrics

- Exact match against the fixed expected output.
- Protected-content pass for names, numbers, URLs, email addresses, paths, filenames, and code tokens.
- Deterministic cleanup latency.

Supporting metrics diagnose the engine. They do not replace the two live user metrics.

## Baseline run

1. Keep commit `4147549` as the untouched automated text baseline. The first live human run uses the current runnable build and records its commit automatically.
2. Keep Fast mode, history off, CPU `int8`, and the bundled `base.en` model.
3. Run `scripts\verify.ps1 -WriteCurrent` to refresh the current text-level evidence. The locked automated baseline is never refreshed during normal verification.
4. Open the separate internal evaluation surface, select Baseline, and start the guided run.
5. Dictate all 20 fixed prompts in order. Accept, edit, retry, or reject each result honestly.
6. Do not retain raw audio.
7. Calculate the two live baseline metrics.
8. Set numeric final targets only after this measured run. Record the reason for each target.

The internal evaluation surface exports the completed CSV and JSON locally. The older metadata-only Review export remains available:

```powershell
.\.venv\Scripts\python.exe .\tools\export_review_metrics.py --last 20
```

The exported file contains decisions, edit flags, modes, and timing only. It does not contain transcript or audio.

## Final run

1. Use the same hardware, microphone, room, corpus order, app target, and mode unless the change itself is being tested.
2. Run all automated checks first.
3. Dictate all 20 cases once. Record retries and edits.
4. Compare primary and supporting metrics to baseline and the target.
5. List every failure. Do not hide failed cases behind an average.

## Privacy and local-only check

- Disconnect or block the network during one full run.
- Confirm no hosted process or API call is required.
- Confirm raw audio is removed after each case.
- Confirm history-off mode stores no transcript.
- Keep personal dictation out of GPT. GPT receives only approved requirements, synthetic corpus text, results, and human review notes.

## Required evidence

- Git commit IDs.
- `baseline_results.json` and final results JSON.
- `baseline_results.json` is locked evidence from the untouched build. Normal automated runs write `current_results.json`; replacing the baseline requires the explicit `--replace-baseline` flag.
- Completed manual review CSV.
- Test output.
- App and installer checksums.
- Network-blocked test result.
- Before/after table.
- Known failures and risks.
