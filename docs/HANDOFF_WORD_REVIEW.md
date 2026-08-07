# Handoff: correction quality, Manuscript Windows Version 2

Rewritten 2026-08-06. Everything below is about `Manuscript/Windows Version 2`,
which is its own git repository. The older `Manuscript/Windows` tree is
superseded.

## The skipped section, narrowed (2026-08-07)

**Symptom, reported by the owner across three days:** the review skips sections
- the counter goes 1 then 4 - and the card reads "No corrected text was
returned. Keep the original text."

**It is not the corrector.** Essay 1 was split into sections using the HUD's own
`_group_sentence_suggestions`, on text with Word's carriage returns, and every
one of the ten sections was then corrected by the real engine:
`llm=True`, changed, all ten. Reproduce with the snippet in this file's history;
it takes about two minutes and rules the engine out before anyone touches it.

**Where it actually is.** That message comes from `rewrite_palette._set_suggested_text`
(around line 2099) and fires only when *both* the original and the replacement
are empty:

    visible = replacement if replacement.strip() else original
    if not visible.strip():
        visible = "No corrected text was returned. Keep the original text."

The live caller is line 1900, `self._set_suggested_text(str(suggestion.original), replacement)`.
So the section arrives with an **empty `suggestion.original`**. It is an
offsets/slicing bug in building the section, not a failed correction, and every
minute spent on guards is a minute in the wrong file.

**The Word-specific lead.** The three text views of the same document disagree:

| view | characters |
|---|---|
| Word COM `Content.Text` | 5217 |
| COM after `_word_visible_text_and_spans` | 5216 |
| what the HUD captured over UI Automation | 5203 |

Thirteen characters between the UIA text the suggestions are computed on and the
COM text everything else uses. The document has ten `
` and no `
`, which is
also why the review logs `paragraphs=1` for a ten-paragraph essay. Check whether
a grouped section's span is being sliced against a different string from the one
it was measured on.

**Next step:** log `suggestion.original` length and span for each grouped section
in `_analyze_suggestions`, run one Word review, and find the section whose span
is non-empty but whose slice is not.

## Plan for the errors that survive correction (2026-08-07)

Essays 1 and 2 now run end to end in Word: 10 sections, 10 accepted, 0 skipped,
about a minute each, and the **grammar error score goes 27 -> 0 and 32 -> 0**.
What is left is a short, specific list. In priority order, with the evidence.

### 1. The bundled dictionary has no inflected forms. Fix this first.

`en_US.dic` holds 49,568 entries and ships beside an `en_US.aff` with **73
affix rules**, and the loader reads only the word list, stripping the `/flags`
that point at those rules. So the dictionary knows `cat` but not `cats`,
`change` but not `changes`, `answer` but not `answering`, and does not know
`after` at all - 48,262 base forms and nothing else.

    .venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'src'); from echo_link_core.polish_spelling import EnglishDictionary; d=EnglishDictionary(); d.warm(); print([(w, d.contains(w)) for w in ('cat','cats','change','changes','after')])"

Everything downstream inherits it. The speller cannot tell a correct word from
a misspelled one across most of ordinary English, which is the same machinery
that produced `esay` -> `ebay` and `cair` -> `chair`. It also makes the
"typos left" column in `correction_quality_scorer.py` meaningless - the ~2200
it reports across the corpus is mostly plurals, and that is why the number
never moved no matter what was fixed.

Expand the affix rules at load. Seventy-three rules is a contained job, and it
is the single largest lever left. Re-run the scorer afterwards; treat every
"typos left" figure recorded before it as void.

### 2. `cair` is still not repaired

Both essays end with "Cair routines". The speller now declines to guess between
`care` and `chair` rather than picking the commoner word, which is the right
trade, but the model does not fix it either, so a wrong word became an
uncorrected one. Revisit once (1) lands, because a dictionary that knows real
words changes what the ambiguity gate sees.

### 3. A lowercase place name can be turned into a common noun

`why cats is good pets in bath` came back `Why cats are good pets in **a**
bath`. The essay is about Bath, England, and says so with a capital B in a
later paragraph. Nothing flags it: `_named_entities` finds no entity in either
side, and the validator returns no failures.

    entities in source : []   entities in candidate : []   failures: ()

The guard cannot see it because a section is validated on its own while the
evidence is elsewhere in the document. The capture is now `scope="document"`,
so the whole text *is* available - the work is plumbing it to the validator as
context, then refusing an article inserted before a token that appears
capitalised elsewhere. **Do not fix this with a word list**; that is the
fixture-overfitting this file already warns about.

### 4. Sentence-initial words the model leaves alone

Essay 1 still opens a sentence with `Wen somebody says`. Low severity, and
likely to move on its own once (1) lands.

### How to measure any of it

`tools/model_coverage_probe.py` for how many paragraphs the model reached, and
the grammar error score, which is trustworthy. Not "typos left", until (1) is
done.

## The state in one paragraph

The Word review loop works mechanically: badge, click, review each section,
accept, next. Essays 1 and 2 were driven end to end in real Microsoft Word,
8/8 and 7/7 sections, zero skips. What was wrong was the corrections
themselves, and the largest cause turned out not to be the model at all - the
local AI was barely running. Two separate faults were switching it off, both
silently. Both are fixed. The corpus has not yet been re-measured with the AI
actually working, and that measurement is the next thing anyone should do.

## The section that says "corrected" and was not (2026-08-06, later)

**A section whose AI answer was refused still shows a review card, still says
Accept, and still counts as a completed section.** It carries the deterministic
rules repair instead. From outside the engine - from the HUD, from a probe, from
the harness report - it is indistinguishable from a full correction. A run of
twenty paragraphs where nine were rules-only was reported as twenty successes,
and nothing in the log said otherwise.

That is the defect to hold in mind when reading any pass rate in this file.
`tools/model_coverage_probe.py` is the measurement that cannot be fooled by it:
one row per paragraph, `used_llm` and the guard that refused it.

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe tools\model_coverage_probe.py --label after-my-change
.\.venv\Scripts\python.exe tools\refusal_evidence_probe.py --essays 1,2
```

The second one writes the answer each guard refused next to the writer's text,
which is the only way to judge whether a refusal was right. Read it before
touching a guard.

### What the refusals actually were

Almost none were meaning errors. The meaning guards count exact words, and the
writer's text is full of typos, so a model that spelled a tracked word correctly
looked like a model that invented one:

| writer | model | guard said |
|---|---|---|
| `befor` | before | date or ordering relationship changed (added before) |
| `cud work real good` | could work really well | modal meaning changed |
| `shoud` | should | obligation strength changed (added should) |
| `Thier is` | There are | named entity removed |
| `Utercht`, `Csco` | Utrecht, Cusco | named entity removed / new named entity added |
| `tHE oner` | the owner | code or identifiers changed |
| `with out proper cair` | without proper care | negation changed |
| `may to be honest NOT greet` | may not greet | prohibition changed |

Every row is the product refusing a correct correction, and each one cost a
whole paragraph. Fixed, in the order they matter:

1. **Four validate call sites were judging against the raw source** instead of
   the spelling-repaired text the model was handed. An earlier session had
   already established `prepared` as the right reference; these four never got
   it, and they are the sites that decide a paragraph's fallback.
2. **`_reconcile_guard_vocabulary`** spells the guards' own closed-class
   vocabulary in the source, using **the candidate as the evidence**. A source
   token is only respelled when the candidate actually uses that guard word and
   has dropped the source token. This is deliberate: a first version rewrote the
   source on its own and turned `wether` into `neither` and `lights` into
   `might`, inventing the negation and modal it was checking. **Do not remove
   the candidate-as-evidence requirement.** A dictionary cannot replace it -
   `cud` is a real word, so no speller will ever call it a typo.
3. **`_edit_distance_at_most` is now Damerau-Levenshtein.** A swapped letter pair
   is one typo, not two, which is what puts `Utercht` within reach of `Utrecht`.
4. **Named entities**: a misspelled function word is not a name
   (`_misspelled_non_entity_starter`, which generalises the hand-added `Teh` in
   the ignore list), and a respelled name is not a removed or invented one
   (`_named_entity_respelled_in`). The ending rule in `_same_name_respelled` is
   load-bearing: `Pett` -> `Pet` and `Csco` -> `Cusco` are typos, `Malik` ->
   `Mali` is a different person. A unit test catches that one; it caught me.
5. **`_IDENTIFIER` camelCase needs a lowercase letter after the first capital.**
   A held shift key produces `tHE` and `iF`; the source-code guard was refusing
   ordinary typos. `getUser`, `eBay` and `iPhone` still match.
6. **Filler is stripped before anything is counted** (`_strip_discourse_hedges`).
   It carries a pronoun and can sit between a modal and its negator, so deleting
   it read as both an actor change and an invented prohibition.
7. **`dense_source` now reaches the sentence-recovery path.** The main path knew
   error-saturated text legitimately moves further when repaired and used a
   lower similarity floor; the recovery path did not, so the same paragraph was
   accepted whole and refused sentence by sentence.
8. **A dropped protected token no longer discards the section when the model
   merged or split sentences** (`_restore_by_sentence_overlap`). Matching is by
   content-word overlap with a clear-best-match requirement, and there is a
   content-loss check, because replacing one candidate sentence with one source
   sentence silently shortens the paragraph when the model had merged two.
9. **Word capture: a bare caret now takes the whole document, not the page.**
   UI Automation cannot see Word's unrendered pages, so a 5203-character essay
   was capped at 3522 and paragraphs 9 and 10 were never offered - while the run
   reported every section it did offer as accepted. A selection the writer made
   is still respected.
10. **Refusals are now recorded.** `CorrectionResult.metrics["refusal_reason"]`
    carries the guard, and `window.py` logs `Writing HUD section fell back to
    rules ... reason=` at WARNING. Startup coming up rules-only logs at WARNING
    with its reason too, instead of one INFO line reading `pack=rules-only`.

Two more, found by re-measuring rather than by reasoning:

11. **A modal's multiplicity is not meaning; its presence is.** The writer wrote
    "then maybe forgets their keys because the maybe morning got busy" and the
    repair dropped the stray second one, refused as "modal meaning changed
    (missing may)". Losing or gaining a modal *family* still fails.
12. **Dropping a redundant "it" subject is allowed**, the mirror of adding one,
    which had been allowed since the fragment repair went in. The literal
    "first alert ... closed it" rule above it is the narrow version of this and
    can go once something else covers it.

13. **The two actor functions disagreed about what counts as an added actor.**
    `_actors_preserved` forgave a noun actor that appeared only because a
    misspelling was fixed; `_added_actors_are_safe_subjects`, which decides the
    same question, did not - so "oner" -> "owner" was forgiven by one and called
    an invented owner by the other. It also has to look at both sides for the
    noun a new "they" refers to: in the paragraph that found this, every noun
    actor is misspelled and therefore invisible on the source side.

Full corpus, 200 paragraphs, all twenty essays:

| | before | after |
|---|---|---|
| paragraphs corrected by the model | 91 | **185** |
| grammar error score after correction | 185 | **13** |

The error score before correction is 622, so the corrector now removes about 98
percent of the measurable grammar errors, against 70 percent at baseline, while
the model reaches twice as many paragraphs. Essays 1 and 2 alone went 11 of 20
to 19 of 20 with a residual of zero. Figures in
`build/model-coverage-verified.json`; that run predates fix 12 and 13 above, so
the four remaining `actor changed (missing it)` rows and the one `added owner,
they` row are already addressed - re-run to confirm before quoting a new number.

### Confirmed after fixes 11-13 (2026-08-06, later still)

Re-ran `tools/model_coverage_probe.py` on an idle machine, which is the check
the note above asks for. **185 of 200 paragraphs, errors 622 -> 13.** That
number is now confirmed rather than projected.

One claim above did not survive the re-run. Fixes 12 and 13 were expected to
clear the `actor changed (missing it)` and `added owner, they` rows; they did
not. Three `missing it` and the one `added owner, they` are still there.
Evidence: `build/model-coverage-confirm-11-13.json`.

The fifteen that remain:

| count | reason |
|---|---|
| 4 | prohibition changed (missing may) |
| 3 | actor changed (missing it) |
| 2 | AI correction was not accepted |
| 1 each | modal meaning changed + actor changed; added owner, they; prohibition changed; modal actor changed; changed protected content; AI output rejected |

### The hedge class: fixed, and the diagnosis that was wrong

**Corrected.** The note below blamed clause scope in `_negated_modal_count`.
That was wrong, and the mistake is worth keeping: *both arms of that branch
append a failure*, so scoping it would only have relabelled the refusal from
"prohibition changed" to "modal meaning changed" and accepted nothing. The gate
is `_modal_meaning_preserved`. Read the branch before believing a label.

The real defect: the writer hedges an open-ended list - "some habitats does
need maybe lights heaters filters or other equipments" - where the hedge says
which items the list holds, not whether the sentence is true. Repairs keep that
openness in words ("or other equipment", "things like lights") and the modal
counter, which only sees literal modals, called each one a lost modal family.
Essay 8 paragraph 5 was refused four times, including the attempt that wrote
the hedge back as "things like" - the meaning preserved exactly and refused
anyway.

Fixed in `fd1f177` with a narrow exemption: the hedge has to follow a lexical
verb, which is not a modal position, and the candidate has to keep the list
open. Without the verb requirement it accepted "students may use the lab or
other rooms" -> "students use the lab or other rooms". `have`/`has`/`had` are
excluded because "you have may seen" is a garbled modal position rather than a
hedged object. Essay 7 paragraph 5 is untouched and still refuses, as it
should. Corpus: **186 of 200, errors 622 -> 12.**

### The original, superseded diagnosis

Four of the fifteen are `prohibition changed (missing may)`, and at least two of
those are the guard being wrong. From `build/refusal-evidence.md`, essay 8
paragraph 5:

> **Writer:** ...it dont need batteries although although some habitats does
> need **maybe** lights heaters filters or other equipments.
>
> **Refused:** It is not a movie, and it does not need batteries, although some
> habitats do need lights, heaters, filters, or other equipment.

The dropped `maybe` sits in the `although some habitats do need...` clause. The
negation it is being judged against - `does not need batteries` - is in a
different clause. The `elif` branch at `polish.py:779` calls
`_negated_modal_count` on the whole normalised text, so any modal that moves
anywhere in a text containing a negation is labelled a changed prohibition.
Clause scope is the fix.

The smoking gun is the fourth refusal of that same paragraph. The model wrote
`do need **things like** lights, heaters...` - the hedge preserved, in different
words - and was refused anyway, because the check counts the literal token and
cannot see a hedge expressed any other way.

**Do not narrow this without re-checking essay-7 p5.** That refusal is correct
(`its needds is maybe MET` -> `its needs are met` turns a hedge into a claim)
and it may rely on the same whole-text scope, so a clause-scoped rewrite could
silently lose it. Run `tools/refusal_evidence_probe.py --essays 7,8` and
confirm 7 still refuses and 8 no longer does, before and after.

### The actor refusals, examined one by one

All six were read against the writer's text. **Five are the guard working.**
Do not "fix" them:

- `essay-6 p6`, `essay-17 p8`, `essay-19 p4` are the *same sentence*: "which is
  why it is good but also not" -> "This is good but also not". The repair drops
  the causal link back to the paragraph. The label says "missing it", which is
  the wrong reason for the right answer, but the answer is right.
- `essay-14 p9`: the writer's "thier IS emotioonal benefits" is existential -
  they meant "There are emotional benefits". The model wrote "Their emotional
  benefits are as well", a possessive. `_normalize_actor_homophones` reads the
  source correctly as "there IS" and the candidate as "Their", so the added
  actor is real and the refusal is correct.
- `essay-11 p4` carries two flags. The actor one is a genuine false positive -
  "would be exagerating it" -> "would be an exaggeration" drops a dummy object,
  not an actor - but the modal flag on the same paragraph is defensible, so
  clearing the actor half alone would not release it. Left alone.

One was a real defect, fixed in `38225c9`:

- `essay-19 p6`, `modal actor changed`. `_modal_subject_counter` was the one
  counter in the validator still reading `meaning_reference` instead of the
  reconciled source. "cud" is an ordinary English word, so no speller repairs
  it and `prepared` keeps it; the source then has no modal beside its subject,
  the candidate has "the owner could write", and repairing the spelling read as
  a modal attached to a new actor. Same class as the four validate call sites
  fixed earlier. Corpus: **187 of 200, errors 622 -> 10.**

### Two refusals that are correct, and one that is not

Do not "fix" these two. They are the guards working:

- `essay-7 p5`: the writer wrote "its needds is maybe MET"; the model wrote "its
  needs are met" and dropped the hedge. That is a claim the writer did not make.
  This one only became visible because "maybe" now folds into "may" for
  counting - the guard used to be blind to it.
- `essay-6 p6`: "which is why it is good but also not" -> "This is good but also
  not" drops the clause that connected it to the paragraph.

The one that is a real defect, still open:

- `essay-5 p5` fails with `The result changed protected content.` **The
  protection layer froze the word "Feeding".** The paragraph is a run-on -
  "...peoples need structure Feeding the parrot , replacing watter..." - so
  "Feeding" is capitalized mid-sentence, `_maximal_named_entities` reads it as a
  single-word name, and `_runtime_opaque_entities` replaces it with
  `ECHOLINKTOKEN0X`. The model then has to drop the token to fix the run-on,
  which is impossible to satisfy. Three retries and the deterministic restore
  all fail, correctly.

  The `ignored` set in `_named_entities` already contains "Cleaning" - a
  hand-added gerund from this same corpus, which is the tell. **Do not fix this
  by loosening name protection**; single-word name protection is what stops
  `Malik` -> `Mali`, and it is the guard this corpus needs most. The fix belongs
  in deciding whether a capitalized word is a name at all, and needs to
  distinguish an unpunctuated sentence start from a genuine mid-sentence name.
  It costs one paragraph in twenty essays, and that paragraph still gets the
  rules repair (4 errors -> 1), so it is not urgent.

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

## "0 of 0 and no original sentence" (2026-08-06, found by using the app)

A writer pressed the Writing HUD shortcut in Word with the caret in the document
and got a review card reading "No sentence available", "0 of 0", and two empty
panes headed "Original sentence" and "Suggested text". Two separate faults:

1. **The shortcut never asked for Word scope widening.** The automatic badge
   built its reader with `include_word_page=True` and the shortcut did not, so
   the same caret in the same document produced a whole review one way and
   `Writing HUD capture failed: No selected text was found` the other. Both
   readers are now built the same way, and in Word a caret with no selection
   takes the whole document.
2. **An empty review still drew a review card.** The card said "No sentence
   available - capture editable text again" over blank panes, which is
   indistinguishable from the app having lost the writer's text, and gave the
   same words whether nothing was captured or the text was already clean. Those
   are now two different messages, the failed-capture one names Protected View
   as a cause, and the empty panes and their headings are hidden rather than
   shown empty.

`tools/restart_app_and_wait.py` restarts the app from source and blocks until
its own log says the model loaded, returning non-zero if it came up rules-only.
Use it before any live run: several runs here were driven against an app that
did not contain the code being tested.

## Rules that cost time when ignored

- **Measure with the code you are measuring.** Three full-corpus runs here were
  thrown away because the probe imports the module once at start, so edits made
  during a twenty-minute run do not apply and the report is a mixture. Freeze
  the code, then run.
- **A guard fix can trade one false refusal for another.** Folding "maybe" into
  "may" fixed one paragraph and broke a different one in the same run, and the
  second break turned out to be the guard correctly catching a dropped hedge.
  Re-measure the whole corpus after every guard change, not the paragraph you
  were looking at.

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
