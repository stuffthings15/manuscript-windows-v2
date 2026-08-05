# Handoff: Word review loop, Manuscript Windows Version 2

Written 2026-08-04. Everything below is about `Manuscript/Windows Version 2`,
which is its own git repository. The older `Manuscript/Windows` tree is
superseded; work from an earlier session is parked there in
`stash@{0}` of the outer `Echo-Link` repo and has **not** been ported.

## The goal

Take one of the 20 animal-pet essays, open it in Microsoft Word, and get all
the way through a page using the Writing HUD the way a writer would: badge
appears, click it, review each section, accept, move to the next. Fix
whatever breaks, confirm the fix, run the same essay again, then move to the
next essay.

Corpus: `test-corpus/animal-pet-20` (integrity-locked, `source.zip` must hash
`C80A6FBA0978596A464E1E9FF3C17D17D6A928FA56445A97C8E045A0EB9FF771`). It is
byte-identical to `Manuscript/20_Animal_Pet_Essays_800_Percent_More_Errors`,
so either location holds the same essays.

## How to run

```powershell
# app from source, no six-minute package build
.\.venv\Scripts\python.exe .\tools\run_dev_app.py

# drive the essay that is open in Word
$env:PYTHONPATH = "<repo>\src"
.\.venv\Scripts\python.exe .\tools\word_essay_probe.py --first-wait 180 --section-wait 150
```

`tools/word_essay_probe.py` is new. Every other probe drives Notepad, which
is not where this app is used; several hours were lost to runs that looked
healthy in Notepad while Word was broken.

Useful, fast, read-only:

- `tools/word_capture_diff_probe.py` - compares the active document as COM
  sees it against UI Automation. Prints the first differing character.
- `tools/repair_probe.py` - runs text through the correction engine and
  prints which check refused the model's answer. Seconds, no UI.

App log: `%LOCALAPPDATA%\ManuscriptWindowsV2\echo-link.log`.

## Fixed and verified this session

1. **Word COM and UI Automation disagreed about the non-breaking space.**
   COM read the document as 5204 characters starting with U+00A0; UI
   Automation read 5203 without it. `_WORD_UIA_OMITTED_CHARACTERS` in
   `src/echo_link_core/insertion.py` existed for exactly this class but only
   listed U+0001. Now lists U+00A0 as well. Verified live: both sides report
   5203 characters and the same digest.

2. **Capture and replacement digested different text.**
   `windows_selection._text_digest` hashed raw accessibility text, where Word
   uses a bare carriage return per paragraph, while the replacement path
   hashed COM text after normalising line endings and dropping COM-only
   characters. They could never agree on any document with more than one
   paragraph. `_text_digest` now canonicalises the same way. Regression test:
   `tests/test_windows_selection.py::test_capture_and_replacement_agree_on_a_word_document_digest`.

3. **The fast model was never installed.** The catalog expects
   `gemma4:e2b-it-qat` and `gemma4:e4b-it-qat`; neither was pulled, so the
   only usable pack was the 12B, and when it did not fit the app logged
   "No validated model pack fits" and did no AI at all. `e2b` has now been
   pulled. Corrections that took 24+ seconds now take 1-5.
   `gemma4:e4b-it-qat` is still missing and `PolishMode.AUTO` prefers it, so
   pulling it is worth trying.

4. **Probes crashed on Word's lock file.** A document open in Word leaves
   `~$name.docx` beside it, and the corpus probe globbed `*.docx` and died
   with `BadZipFile`. `_essay_files()` now skips `~$`.

5. **The probe clicked "Open Manuscript settings" instead of the badge.**
   `_badge_button()` returned the first enabled button in the HUD, which is
   the settings gear. The dialog it opened then held the foreground, so Word
   could not be brought forward and every capture read the dialog instead of
   the document - the app reported "no selected text" on a fully selected
   essay. `_badge_button()` now looks for the review badge by name.

6. **Word never came to the foreground** from a background caller.
   `focus_word()` in the Word probe now attaches to the target thread's input
   queue before calling `SetForegroundWindow`.

7. **The HUD counter was never read.** The accessible name is "Current
   writing suggestion position, 1 of 8", and `_counter_text()` required the
   whole name to be "1 of 8", so every section looked like it had no counter
   and runs stopped as out of order.

## The open bug - this is where to start

Accept still does not reach the document. The user sees:

> This correction was not applied. The document may have been busy. Try
> Replace Text again.

and after clicking through, the next section shows the whole essay as one
suggestion and the counter reads "0 of 0".

The log shows the cause, repeatedly:

```
Writing HUD Replace Text invoked ... original_chars=37 replacement_chars=36
Word COM range replacement refused changed document text
Writing HUD immediate apply could not recheck the source text; retry 1 of 2
Writing HUD immediate apply failed 2 times; asking for a fresh capture
Writing HUD immediate Accept was not applied
```

`_replace_palette_step` in `src/echo_link_app/window.py` calls
`replace_document_range`, which returns `None`, which produces the "busy"
message. The refusal comes from one of the two guards in
`insertion.replace_document_range` (around lines 336-360).

**Diagnostics are already in place.** Both refusal branches now log which
comparison failed, the character counts, and the first differing character.
Reproduce once and read the log line - it will say directly whether the
document digest disagrees or the captured range text disagrees, and where.
That single log line should end the guessing.

Two observations that likely matter:

- The failing capture is `scope=selection chars=3523`, not the whole 5203
  character document, because the reader expands to the current Word page.
  So the range guard is the one being exercised, not the whole-document one.
- After the second failure the app asks for a fresh capture. Word still has
  everything selected, so the new capture is the entire essay as a single
  section, which is exactly the "whole essay as one suggestion, 0 of 0"
  behaviour being reported. Fixing the refusal should remove that symptom
  too, but the collapse to one giant section on recapture is worth a look on
  its own.

Also seen and not yet investigated: the model repeatedly drops a protected
token - `protected-token mismatch expected=('ECHOLINKTOKEN1X',
'ECHOLINKTOKEN0X') actual=('ECHOLINKTOKEN0X',)` through initial, retry and
final-retry - so some sections never produce a usable correction.

## State of the tree

Nothing has been committed this session. Changed files:

- `src/echo_link_core/insertion.py` - NBSP fix, plus temporary diagnostic
  logging in the two refusal branches. **Remove the diagnostics once the
  cause is found**, or keep them if they read well.
- `src/echo_link_core/windows_selection.py` - `_text_digest` canonicalisation
  and the `_COM_ONLY_CHARACTERS` set.
- `tools/full_page_user_journey_probe.py` - badge and counter fixes.
- `tools/animal_essay_corpus_probe.py` - `~$` lock-file skip.
- `tools/word_essay_probe.py` - new.
- `tests/test_windows_selection.py` - updated digest assertion, new
  regression test.
- `docs/HANDOFF_WORD_REVIEW.md` - this file.

`tests/test_insertion.py` and `tests/test_windows_selection.py` pass, 96
tests. The full suite has not been run since these changes.
