# Handoff: correction quality, Manuscript Windows Version 2

Rewritten 2026-08-06. Everything below is about `Manuscript/Windows Version 2`,
which is its own git repository. The older `Manuscript/Windows` tree is
superseded.

## The state in one paragraph

The Word review loop works mechanically: badge, click, review each section,
accept, next. Essays 1 and 2 were driven end to end in real Microsoft Word,
8/8 and 7/7 sections, zero skips. What was wrong was the corrections
themselves, and the largest cause turned out not to be the model at all - the
local AI was barely running. Two separate faults were switching it off, both
silently. Both are fixed. The corpus has not yet been re-measured with the AI
actually working, and that measurement is the next thing anyone should do.

## Fixed and committed

`8697fa4` - stop the corrector rewriting words the writer chose

1. **The AI was not running at all.** An earlier session edited
   `resources/polish/model-packs.json` without re-signing it, so every pack
   failed with `ModelPackSecurityError: Polish model manifest signature
   failed.` and the engine fell back to deterministic rules. The only surface
   was `full AI correction did not run` inside a probe; the HUD said nothing.
   Fixed by running `tools/sign_polish_manifest.ps1`.

   **If corrections ever look suddenly worse, check this first:**

   ```powershell
   $env:PYTHONPATH = "src"
   .\.venv\Scripts\python.exe -c "from echo_link_core.polish_model_packs import ModelPackManager; [print(p.pack_id, p.available) for p in ModelPackManager().catalog()]"
   ```

2. **The speller was inserting wrong words before the model saw the text.**
   Three causes, all in `src/echo_link_core/polish_spelling.py`:
   - The bundled SymSpell frequency file opens with a byte order mark and was
     read as plain `utf-8`, so its first entry was stored as `\ufeffthe`. The
     most common word in English could never be offered and `teh` became
     `tech`. Now `utf-8-sig`.
   - `_typo_shape_rank` had no transposition category, so every swapped pair
     fell in beside a plain substitution and lost on frequency: `wehre` became
     `were`, `chroes` became `chromes`.
   - Where two candidates are equally good repairs, frequency decided, and the
     commoner word is often not the meant one: `cair` became `chair` three
     times in one essay. The speller now declines to guess and leaves the word
     for the model, which reads the sentence. A 50x frequency margin still
     resolves the obvious cases so `teh` and `animul` are not left on screen.

3. **Corrections were changing meaning.** `ENTERS the ROOM` came back
   `measures the room`; `grete a person` came back `get as much as`. New
   `src/echo_link_core/meaning_diff.py` asks which content words vanished with
   no respelling or inflection to explain them. Proofread refuses those.
   Proofread only - every other command routed through that guard is asking
   for a rewrite, where replacing a word is the requested work.

`bc41bbd` - one refused correction no longer disables the AI for the rest

A failed pack is held out of service for five minutes so a broken runtime is
not retried on every section. That was applied to validation failures too, so
one paragraph whose answer was refused blacklisted the pack and every later
paragraph ran rules-only. **Across twenty essays the AI reached three
paragraphs out of two hundred.** Validation failures now expire in five
seconds; real runtime faults keep the full window.

## The measurement harness

`tools/correction_quality_scorer.py` scores the whole corpus headless, so a
guard change is judged against twenty essays instead of one. It shares
`meaning_diff` with the product, so the number it reports and the rule the
product enforces cannot drift apart.

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe tools\correction_quality_scorer.py --label after-fixes
```

Reports per essay: meaning changes (must be 0), wrong speller substitutions
(must be 0), typos left after (the counterweight - a guard that refuses
everything shows up here), error score before and after, protected-token drops.

**The baseline in `build/correction-quality-baseline.md` is not trustworthy.**
It was taken while the AI reached 3 of 200 paragraphs, so it mostly measures
the rule engine. Re-run it now that both shutoff faults are fixed, and treat
that as the real baseline.

## Still open, in priority order

0. **A long-lived engine degrades permanently.** This is the top item and it is
   reproducible. Run the scorer over the corpus: essays 1-3 use the model, then
   essay 4 onward returns in 0.0 seconds on every paragraph with one identical
   stale message, `validation failed: modal meaning changed; obligation
   strength changed (added hard requirement, should)` - including paragraph 1,
   which the engine never saw. Zero seconds means no model call was made.

   A fresh `PolishEngine` corrects the same essay 4 paragraphs normally:

   ```powershell
   $env:PYTHONPATH = "src"
   .\.venv\Scripts\python.exe -c "import sys; sys.path[:0]=['src','tools']; from echo_link_core.polish import PolishEngine; from echo_link_core.polish_types import CorrectionRequest, PolishMode, PolishTask; from animal_essay_corpus_probe import CORPUS, load_essays; paras=[p for p in str(load_essays(CORPUS)[3]['text']).split(chr(10)) if p.strip()]; eng=PolishEngine(); print([eng.analyze(CorrectionRequest(text=p, task=PolishTask.PROOFREAD, mode=PolishMode('balanced'), command='Proofread', download_consent=True, manual=True)).used_llm for p in paras[:2]])"
   ```

   `_failed_pack_ids()` is empty at the time of failure, so this is not the
   blacklist fixed in `bc41bbd`. Something else on the engine caches a
   downgraded plan across requests and never recovers. The app holds one
   long-lived engine, so this is exactly the "corrections get worse the longer
   I use it" symptom, and it will hide the result of any other quality work
   until it is fixed. Start from `_RuntimeCascadeState`, `_notify_plan`, and
   anything that memoises a plan on the engine rather than the request.

1. **Protected tokens are dropped by the model** through initial, retry and
   final retry: `expected=('ECHOLINKTOKEN1X','ECHOLINKTOKEN0X')
   actual=('ECHOLINKTOKEN0X',)`. This is what triggers most validation
   failures. Prompting harder has failed three times; restore the token
   deterministically after the final retry, or refuse only that section.
   `_missing_immutable_tokens` in `selected_text.py` already identifies them.

2. **Only one Word page is reviewed.** `windows_selection.py:134` expands the
   selection to the UIA `TextUnit.Page`, and UIA cannot see Word's unrendered
   pages. Paragraphs past page 1 are never offered - essays 1 and 2 both came
   back with paragraphs 8-10 untouched. The apply path already reads the whole
   document over COM (`insertion.py:210`), so only capture is page-bound.

3. **A failed manifest signature is invisible.** It disabled the product's
   main feature with no UI signal at all. Surface it in the status line or
   Settings and log it at WARNING.

4. **`gemma4:e4b-it-qat` is installed but unmeasured.** All three packs verify.
   Compare E2B and E4B on the corpus with the scorer before choosing a default,
   and keep E2B as the small-machine path. 12B stays manual opt-in.

5. **`selected_text.py` is overfitted to fixtures.** It carries rules keyed to
   literal phrases - `ready to launch manuscript`, `three users who wrote
   several pages`, `Reviewers need clear red marks`. Measure whether any of
   them affect real text before deciding anything.

6. Feature sweep by hand, then installer lifecycle. Neither has started.

## Rules that cost time when ignored

- **Test in Word, not Notepad.** Notepad hid the Accept bug for hours.
- **Read the log line, do not guess.** `%LOCALAPPDATA%\ManuscriptWindowsV2\echo-link.log`.
  A latency-gate theory survived two rounds of reasoning here and was wrong;
  the trace said so in one line.
- **Show the corrected text, not test counts.** The suite was green through
  every defect listed above.
- **Commit with `git add -p`, never whole files.** The working tree carries
  large uncommitted work from a parallel session in files you will also be
  editing - `polish.py`, `polish_runtime.py`, `selected_text.py`,
  `test_polish_engine.py`. Staging a whole file sweeps that work into your
  commit, and `git checkout --` on one destroys it. Both happened here. A
  destroyed file is recoverable from the dropped stash commit if you still
  have its hash from the `git stash pop` output.
- **The corpus is integrity-locked.** `test-corpus/animal-pet-20/source.zip`
  must keep hashing
  `C80A6FBA0978596A464E1E9FF3C17D17D6A928FA56445A97C8E045A0EB9FF771`.
  Corrected output goes to `build/corrected/` as copies.
- Run the suite with
  `--basetemp=C:/Users/stuff/AppData/Local/Temp/mspt`; without it 31 tests fail
  on a `PermissionError` that is environment, not code.
