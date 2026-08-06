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

**Measure on an idle machine.** The policy pauses the local AI below 4 GB free
RAM, which is correct, but it means a run competing with a test suite or a
second probe measures the machine rather than the product. Several runs here
were wasted that way; the giveaway is essays completing in under a second.

Current figures, twenty essays, E2B, `build/correction-quality-final.md`:

| | baseline | after all fixes |
|---|---|---|
| paragraphs the model corrected | 3 of 200 | **91 of 200** |
| grammar error score after | 185 | **122** |
| wrong speller substitutions | 70 | **8**, all scorer false positives |
| protected tokens dropped | 0 | 0 |
| essays where the error score rose | 0 | 0 |

The error score before correction is 555, so the corrector now removes about
78 percent of the measurable grammar errors, against 67 percent at baseline -
while making the model reach thirty times as many paragraphs.

The "swaps" column, 99, is still not trustworthy enough to quote. It counts
content words missing from the final text, so on a paragraph the model did not
correct it reports losses made by the deterministic rules, and
`describe_losses` names the nearest new word, which produces noise like
`one -> pet`. The guard it shares code with is tested directly and in both
directions; the corpus-wide number is not yet a clean measure of it.

## Still open, in priority order

0. ~~A long-lived engine degrades permanently.~~ **Fixed** in `4eddb63` and
   `ccc7015`. It was three separate faults stacked: the fallback cascade filled
   the GPU with the app's own models and `_resource_reason` read that as a full
   card; the loaded-model query timed out while the server was generating and a
   lost answer was read as "nothing loaded"; and the validation-failure window
   was longer than a rules-only paragraph, so one refusal covered the next ten
   and one of those re-armed it. Kept here because the shape recurs.

   Original symptom, for reference: Run the scorer over the corpus: essays 1-3 use the model, then
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

## Word automation: what works and what does not (2026-08-06)

`tools/word_essay_session.py` is the harness that produces the file worth
reading - `build/corrected/<name>.review.md`, one paragraph per row, Was and
Now. It refuses to start when the local AI has fallen back to the rules, and
it reports a short capture instead of hiding one.

**It worked once, driven against a document a person had already opened and
left in front.** 8 sections, 44 seconds, real corrections. That run is what
found both product defects listed below.

**Seven consecutive runs after that failed, all in the harness, never in the
app.** Every one ended with the app saying `Writing HUD capture failed: No
selected text was found` while Word had the whole document selected through
COM. Things tried and ruled out, each disproved by one command:

- Page-bound capture. Wrong: UI Automation reports the whole 5187-character
  document, not a page.
- Protected View. Wrong: the working copy carries no Zone.Identifier.
- Stale badge from the caret placed on open. Real, and fixed - the badge went
  from 8 sections to 11 - but not the cause of the capture failure.
- Dismissing the stale badge after selecting. Real, and fixed: it pulled
  focus off Word and dropped the selection.
- Ten leftover Word windows, several in Protected View, with
  `focus_word` matching any title ending "- Word". Real, and fixed.
- Removing the focus step entirely in `--use-open-document`. Made it worse:
  PowerShell was then the foreground window when the hotkey fired.

What is left: driving `Ctrl+Shift+Space` from a background process does not
reliably present Word's selection to the app, even with Word foregrounded and
a single window open. The app is not at fault in any of these traces - its
log shows `section counter start -> 1 of 11 (available=11 visible=11
total=11)`, so it captured the whole essay and offered every section.

**Do not spend another session on the hotkey.** Two better routes:

1. Use the HUD's own **Check entire document** button rather than a global
   hotkey and a selection. It asks the app to fetch the document itself,
   which removes the selection handoff completely.
2. Have a person select the text and press the hotkey, then let the harness
   take over from the badge onwards. That is the path that worked.

## The two product defects this found

1. `practice esay 1??` became `practice **is easy** 1?`. A meaning
   corruption, same family as the earlier `esay` -> `ebay`.
2. **Protected View is unhandled.** A document opened from a downloaded
   location is read-only, and the app reports "No selected text was found"
   rather than saying the document needs Enable Editing. A writer opening an
   emailed or downloaded draft hits this immediately.

## "esay" -> "is easy": why the obvious guard is the wrong fix

Paragraph 2 of essay 1 came back as `practice is easy 1?` where the writer
wrote `practice esay 1??`. The word meant is `essay`.

The meaning guard let it through because `unexplained_losses` asks whether
any word the candidate introduced explains a word that vanished, and
`_explains("esay", "easy")` is true - they are one edit apart. From a bag of
words it looks exactly like a respelling.

The tempting fix is to refuse a substitution whenever the bundled dictionary
declined to repair the source word, on the grounds that the model is then
guessing. **Do not do this.** The dictionary declines exactly two kinds of
word in this corpus:

| source | dictionary | model | verdict |
|---|---|---|---|
| `esay` | declines | `is easy` | wrong |
| `oner` | declines | `owner` | right |

`oner` -> `owner` is one of the better corrections in the whole essay, and it
is right because the model read the sentence. Refusing model substitutions on
declined words would throw that away, and it would undo the deliberate
decision recorded above: the speller stops guessing so that the model, which
has the context, can decide.

So this is a model-quality miss, not a guard defect, and it costs more to
guard than it costs to leave. Worth revisiting only with a rule that
distinguishes a respelling from a restructure - `esay` became *two* words,
`is easy`, which is not a spelling repair at all. A bag-of-words comparison
cannot see that; a positional diff could.
