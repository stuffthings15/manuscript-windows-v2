# Full-Page User Journey Repair - 2026-08-01 (night session)

Curtis asked for a user-level proof: launch the app, feed it whole pages of
error-heavy text, click through every correction to completion, and keep the
waits short. `tools/full_page_user_journey_probe.py` does exactly that
against real Notepad and the live Writing HUD: it opens a document, clicks
the badge, accepts every review section, times each wait, and scores the
final text for remaining seeded errors and preserved protected values.

## Defects found and fixed (in click-through order)

1. **Accepted corrections stopped applying after the first section**
   (`window.py`, `insertion.py`). After a whole-document apply, the next
   section's record was synthesized from the pasted text with `\n` breaks
   while Notepad reports `\r`, so every later recheck failed as "document
   changed" and Replace Text silently did nothing - the "old text still on
   it" report. The record is now re-read from the editor after each apply,
   and the recheck compares newline-normalized text. Recheck failures also
   log their exact reason now (content-free).
2. **First correction paid the model cold load** (`window.py`). Startup
   "readiness" planned but never loaded the model. The startup thread now
   runs the acceptance probe and a one-token warm generation; the model is
   resident before the first user request (16s first wait became ~4s).
3. **Fast accepts could apply rules-only text** (`rewrite_palette.py`). The
   AI gate only locked Replace Text while the current section owned the
   active AI request; a section whose request was queued behind a prefetch
   enabled Accept with the deterministic placeholder. The gate now locks
   until the section has a finished AI result and issues the request itself
   if nothing is generating.
4. **Series progress + ETA** (`rewrite_palette.py`, per Curtis). Both the
   loading page and the big card now show a progress bar (sections AI-ready
   out of total) and a rolling-average ETA until all sections are ready.
   During a series the HUD stays on the big card instead of bouncing to the
   small loading window; only the controls lock while a section finishes.
5. **Bigger batches** (`rewrite_palette.py`). Review sections grow to six
   sentences. They deliberately do not span paragraph breaks: both local
   models kept merging multi-paragraph requests into one paragraph, so
   paragraph-per-section is the reliable shape.
6. **Bare `\r` paragraphs confused the model** (`rewrite_palette.py`).
   Section text is sent to the model with standard `\n` breaks.
7. **Validator false-rejects burned correct output** (`polish.py`):
   capitalizing a lowercase date ("august 18" -> "August 18") read as a
   protected-fact change; "wifi" -> "Wi-Fi" as a new named entity;
   "their is" / "there laptops" homophone repairs as actor changes;
   "eight user" -> "eight users" as an actor change (noun actors now
   compare singular); "visitor cant" -> "visitors cannot" as a modal actor
   change ("cannot" now normalizes to "can't").
8. **Written-number facts quoted broken plurals** (`selected_text.py`).
   "two table", "eight user" were marked immutable in the prompt; the
   phrase repair now pluralizes any counted noun, not just time units.
9. **Greedy retries replayed the rejected answer** (`selected_text.py`).
   Safety retries now sample (temperature 0.4, prompt-derived seed) while
   first attempts stay greedy-deterministic.

## Final measured state (this machine, gemma4:12b on CUDA)

- 3-paragraph page: 3 sections, 24s total, max wait 4.1s, zero errors left.
- 25-paragraph (3-page) document: 25 sections, ~2 minutes total, max wait
  4.4s, mean 2s, no stalls, no repeats, HUD stays open to completion, all
  14 protected values intact. 5 of ~70 seeded errors survive as residual
  model misses ("finish there"/"two table was" - the model insists on a
  they->it swap the validator rightly blocks; "Employee's"; "docs site
  have"; "flag three"). Each survives safely as deterministic text, never
  as a corruption.
- Focused suites: 667 tests pass.

Evidence: `build/full-page-user-journey-final.json` and the numbered
`build/full-page-user-journey-long*.json` iterations.
