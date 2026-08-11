# Handoff: integrating the production engineering rules

Paste everything below the line into a new session. It is written to be
self-contained: it names the files, the measurements, the decisions already
taken, and the exact next action.

---

You are continuing work already in progress on Manuscript Windows Version 2 at
`C:\Users\stuff\Documents\Echo-Link\Manuscript\Windows Version 2`. Read
`CLAUDE.md` and `docs/ARCHITECTURE.md` in that repository first; they were
written during this work and contain the rules, the architectural map, and the
debt register. The approved plan is at
`C:\Users\stuff\.claude\plans\zesty-doodling-waffle.md`.

Do not re-plan, do not re-measure what is recorded below, and do not start a
rebuild. Pick up at "next action".

## What the work is

Curtis supplied a 50-rule Production-Grade AI Software Engineering Master Prompt
and asked for it to be integrated into the project, which had accumulated bloat
and had not followed the rules. The plan is targeted consolidation, not a
rewrite. Rule 41 governs: 2021 passing tests and several sessions of corpus
measurements are real value.

The headline finding, measured by parsing the source rather than reading it:
**production code memorizes sentences from its own test fixtures.**
`_apply_fast_proofread_rules` in `src/echo_link_core/selected_text.py` (line
2916) is a single 1,692-line function holding 523 regex rules, and 155 of them
carry a literal run of six or more words taken from
`tests/fixtures/grammar_gpt56sol_page_*.json`, `grammar_physical_page_*.json`,
and the animal essay corpus. Strings like `RB-SYNC-64`, `HP-DUP-4`,
`gardens raise rent`, and `caption file stopped after nineteen minutes` exist in
exactly two places: that function and the test asserting it.

That is rule 18's "hard-code test-specific values into production code". Some
green tests prove nothing, and a new essay with the same grammar defect gets no
correction because the fix was pinned to one sentence.

## Decisions already taken by Curtis, do not reopen

1. **Memorization**: generalize where a real rule exists. Where it does not,
   delete the literal, let the test fail, and record it as an explicit known
   defect naming the essay and sentence. Honesty over a green board.
2. **File splits**: all four large files, `polish.py` last.
3. **Static analysis**: ruff, dev-only, added and baselined.
4. `tools/` is kept in full. Do not prune it.

## Non-negotiables

- **Never destroy files.** Never `git checkout --` a file; it has already
  destroyed a session's work once. Revert precisely with edits, or recover a
  version with `git show <rev>:<path>`.
- **Stage with `git add -p`**, never whole files. One commit per verified change.
- `test-corpus/animal-pet-20` is integrity locked. Read from it, never write.
- **Never weaken a safety property or a test** to make something pass.
- **Do not modify the meaning guards.** They may be moved between modules
  unchanged, never edited. Five of six apparent false refusals were correct.
- **No em dashes** in anything. ASCII hyphens only.
- **Ask before long automated Word sessions.** They take the screen for 45
  minutes.
- Do not spawn subagents, workflows, or deep research unless Curtis asks.

## Verification contract

Every commit must satisfy all four:

```bash
.venv/Scripts/python.exe -m pytest -q
```
Baseline **2025 passed, 2 skipped**, takes 9 to 17 minutes. Any drop is a
regression unless it is an intentional known defect recorded in
`docs/ARCHITECTURE.md`.

```bash
.venv/Scripts/python.exe -m ruff check src tests tools
```
Must exit clean.

Review the diff as a hostile reviewer; net line count should fall. And **observe
every regression test failing without its fix** before committing it. This is
not ceremony: two dictation tests were green with their fix removed, and the
first general rule written in this work had a false positive that only the
existing suite caught.

## State: committed

| Commit | What |
|---|---|
| `5028840` | W1. `CLAUDE.md` at the repo root: rules, non-negotiables, the memorization warning |
| `b5df5c3` | W2. `docs/ARCHITECTURE.md`: two-package split, dictation and correction paths, which module owns which decision, debt register |
| `8f14fc8` | W3. ruff 0.14.6 as a `lint` extra, `select = ["F", "BLE", "T20"]`, `BLE001` ignored pending W5. Cleared 21 unused imports, a dead `user32` binding, a dead `observed_ai_busy` flag |
| `6959f62` | W3. Lint command and two debt entries recorded |
| `e9b9716` | W4, first two literals. General serial-comma rule in `writing_suggestions.py` replacing the `estimate includes staffing, security, utilities and transit` literal, plus a byte-identical duplicate rule removed. Suite 2025 passed, 2 skipped |

Earlier in the same session, before the plan: `0f3d507` removed four request
options the adapter discarded, `4e693b8` gated the harness 45s poll on the HUD
readiness signal.

## State: uncommitted in the working tree

Nothing. The tree is clean and the suite reads **2025 passed, 2 skipped** (the
2021 baseline plus the four new serial-comma tests). Ruff exits clean.

The new baseline for every future run is therefore **2025 passed, 2 skipped**,
not 2021.

## A rule already learned, do not relearn it

The serial-comma rule in `writing_suggestions.py` requires **two** preceding commas, not one. The first
version required one and inserted a comma into "Visitors will not pay, although
the city continues to provide staffing and security", because that comma
belonged to a subordinate clause. The deliberate cost: "apples, pears and plums"
is left alone. A wrong comma is worse than a missing one. Do not loosen this
without a better discriminator.

## Next action

1. Apply the **stray-verb rule**, which is designed and verified standalone but
   not yet written into the source. It replaces eleven memorized rules with one.
   Working prototype:

   ```python
   _STRAY_VERB_FORMS = (
       "accessed|based|came|comes|drawn|followed|follows|kept|keeps|needed|"
       "needs|referenced|requested|retained|stemmed|used|wanted|wants"
   )
   _MODAL_STRAY_VERB = re.compile(
       r"\b(?:must|should|shall|will|would|can|could|may|might|does|do|did)\s+"
       r"(?:not\s+)?[a-z]+\s+"
       r"(?P<stray>" + _STRAY_VERB_FORMS + r")\s+"
       r"(?=(?:the|a|an|this|that|these|those|his|her|their|its|our|my|your|"
       r"from|on|of)\b)"
   )
   ```

   Drop the `stray` group only, keeping the text before it. Verified to repair
   all six `must not overwrite needs|needed|retained|accessed|referenced|
   requested the earlier backup` variants, `does not follow stemmed|comes from`,
   and to generalize to `She should not delete kept the file`, which no
   memorized rule covered. Verified to leave alone `Residents must pay taxes the
   council sets` (where `taxes` is a plural noun and deletion would be
   catastrophic), `We must keep tracking the changes`, and
   `They must not overwrite the earlier backup`.

   Put it in `writing_suggestions.py` beside the serial-comma rule, as a GRAMMAR
   candidate, so both the HUD suggestion path and the fast proofread get it.
   Then delete these now-redundant rules from `_apply_fast_proofread_rules`:
   lines 3562, 3568, 3572, 3576, 3755, 4361 (`must not overwrite` family) and
   3044, 3413, 3604, 3739 (`does not follow` family). Verify line numbers before
   editing; they shift as rules are removed.

   **Do not** delete line 3032, `does not follow (?:was|is) based on the
   evidence`. That one needs `based on` replaced by `from`, a substitution the
   deletion rule cannot produce. It belongs with the substitution group.

2. Continue W4 through the remaining groups. The taxonomy, from
   `ast`-parsing the rule table:

   | Class | Count | Notes |
   |---|---|---|
   | substitution | 88 | the hard majority; each needs its own general rule or a known-defect record |
   | deletion | 35 | the stray-verb rule takes about 11 |
   | punctuation-only | 21 | serial comma done; remaining are comma-free lists, clause commas, introductory phrases |
   | computed (lambda) | 6 | |
   | insertion | 5 | |

   Nine of the punctuation-only rules are **comma-free lists**
   (`customer names gate codes and service notes`). Segmenting those needs
   part-of-speech knowledge that this layer does not have, and adding a tagger is
   a new dependency. Under the agreed policy these get deleted and recorded as
   known defects, but **show Curtis the specific sentences before deleting**,
   because it is a real quality loss on the corpus.

## Remaining work items, already scoped

**W5, exception audit.** All 137 broad handlers in `src` are classified
(a scratch script did this; re-derive with `ast` if needed): 73 log with
context, 15 are translated or re-raised, 34 are silent with a defined fallback,
10 silent other, 5 silent `pass`. All five `pass` sites were read and none is a
defect: a clipboard poll loop (`insertion.py:1134`), `CoUninitialize` in
`__exit__` (`transcription.py:117`), and three UI Automation calls
(`windows_selection.py:791, 934, 968`) where the API throws undocumented types.

So W5 is about making reasons visible, not fixing hidden bugs. Rather than 137
`noqa` comments, add a test that asserts the set of silent broad handlers equals
a known justified list, so a new one fails the suite. Then decide whether the
global `BLE001` ignore in `pyproject.toml` can be removed; its comment says
removing it is the last step of this audit.

**W6, duplication sweep.** A normalized-AST comparison across `src` found
exactly four duplicates, all with an obvious canonical home:

- `_sha256` in `polish_model_packs.py:656` and `polish_runtime_packs.py:629`.
  The latter already imports `_verify_rsa_sha256` from the former, so import
  `_sha256` too and delete the copy.
- `_match_case` in `polish_spelling.py:722` and `writing_suggestions.py:1022`.
  `polish_spelling` already imports `SPELLING_CORRECTIONS` from
  `writing_suggestions`; same direction.
- `_GPU_BACKENDS` in `accepted_inference.py:28` and `polish_runtime.py:41`.
- `_TERMINATOR_ABBREVIATIONS` in `selected_text.py:1866` and
  `writing_suggestions.py:784`, identical contents, different formatting.

`writing_suggestions.py` must stay free of I/O and must not import the
dictionary. Keep the dependency direction pointing at it, never out of it.

**W7, splits**, in this order, one commit each, suite green between:

1. `selected_text.py` (5,030 lines, 61 top-level defs). Moving the proofread
   rules into a new `polish_proofread.py` removes a third of the file by itself.
   Other candidates: `_apply_safe_caveman_splits` (377 lines),
   `_linked_source_facts_preserved` (227), `_restore_source_cardinality` (212).
2. `rewrite_palette.py` (3,241, 95 methods): review state machine apart from
   widget layout.
3. `window.py` (4,328, 154 methods): the HUD job queue and AI worker loop are
   domain logic inside a `QMainWindow`.
4. `polish.py` (6,764) last. Seams: meaning guards and entity detection
   (~4885-6700), tone and style rewriting (~3469-4300),
   `PolishWritingAssistantAdapter` (~2995-3445), leaving `PolishValidator` and
   `PolishEngine`. Extends the existing `polish_*` module family.

Each split is mechanical: move, re-export for callers, run the full suite,
commit. If the suite drops by one test the split is wrong; revert that commit.

**W8, end-to-end.** One batch of all 20 essays through the HUD in real Word with
`tools/run_essays_batch.py`, at the end, not per commit. Compare against the last
measured baseline: 16 complete, 3 partial, 0 dead, 7 perfectly clean. W4 will
move these numbers and that movement is the real result of the work. **Ask
Curtis before starting it**; it takes the screen for about 45 minutes.

## Honest status

Three of eight work items are complete. W4 is roughly 2 rules of about 155.
W5, W6 and W7 are fully scoped but not started. W8 has not run.

Of the 320 lines committed so far, 243 are the two documentation files. The
production source change to date is small. The large diff is still ahead, in W4
and W7.
