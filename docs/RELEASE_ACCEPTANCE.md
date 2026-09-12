# Release acceptance

The one place that says whether Manuscript is fit to ship. Before this, the
answer lived across 111 test files, 2344 tests and about seventy probes with no
written thresholds, so "is it ready" was a judgement call every time it was
asked. These numbers are enforced by `tools/release_acceptance.py`, which fails
when one is missed.

Changing a threshold changes what shipping means. Record the decision here and
in `AI Brain/Echo-Link/memory.md`. Never edit one to turn a run green.

## Run it

```bash
.venv/Scripts/python.exe tools/release_acceptance.py --fast
```

```bash
.venv/Scripts/python.exe tools/release_acceptance.py --full
```

`--fast` is about fifteen minutes and is the every-iteration tier. `--full` is
hours; run it once fast passes, or overnight. Both take the screen while Word
is driven, so neither can share the machine.

Evidence lands in `build/acceptance/`.

## The thresholds

| # | Gate | Threshold | Tier |
|---|---|---|---|
| 1 | Corrections, coherent corpus | mean at least 95, no essay below 90 | full |
| 2 | Corrections smoke, 5 essays | mean at least 95 | fast |
| 3 | Tone, each of the six palette tones | at least 95 percent of sections pass | full |
| 4 | Test suite | passes, and ruff is clean | fast |
| 5 | Control surface | no findings | fast |
| 6 | Diagnostics | `diagnostics_check.py` passes | fast |
| 7 | Document shape | no run changes a document's paragraph count | both |

## What each number does and does not prove

**Corrections** are scored by `tools/grade_corpus.py` on five weights -
spelling, homophones, punctuation, grammar, coherence - scaled to 100. They are
measured through Word and the HUD, accepting each section the way a writer
does. Never headless: a headless run scored a corpus at 62 on 2026-08-14 and it
was reported as a product regression when the local AI had simply latched off.

Every Word batch runs `--as-a-writer`: one Word session and one app for the
whole run, documents opened one after another. The default restarts the app and
kills Word between essays, which is isolation and not use. That default exists
because the HUD carried one document's review into the next and reported "4 of
11" for a ten-section essay - and while it is on, that defect cannot happen, so
the number says nothing about what a writer gets on their second document. A
gate that only measures the first document of a freshly started app measures a
situation almost no user is ever in.

The gated corpus is `test-corpus/error-riddled-20-docx`, coherent prose with
seeded surface damage, because that is what a user's writing actually looks
like. `20_Animal_Pet_Essays_800_Percent_More_Errors` has its SCORE reported on every
full run and never gated: it is deliberately incoherent and harder than
anything a user will type, so a low number there is information, not a
blocker. Gate 7 is the exception and applies to it like any other corpus, for
the reason gate 7 exists: a document that came back a different shape is not a
low score, it is damage, and no corpus is too hard to justify losing one of a
writer's paragraphs.

Known limits, stated so a passing score is not read as more than it is.

Coherence has no scored term and is always full marks, which inflates failing
documents rather than passing ones - measured, removing it moves the animal
mean only from 94.8 to 94.6. Organization was removed on 2026-08-19 because it
scored the author's paragraph order, which the product must not change.

The spelling term counts words the bundled dictionaries do not have, and those
dictionaries are incomplete. `cybersecurity`, `microgrid`, `tradeoff`,
`makerspace` and `outplanting` are ordinary English and are in neither the
Hunspell list nor the frequency list, so an essay about solar microgrids is
charged for using the word. They are deliberately not hand-added, because they
are the words in this corpus and adding them is memorising the answer. Read the
`unknown_words` list in the report before treating a low spelling score as a
correction failure: on the first gate run, nineteen of twenty-four charged
words were correct writing.

Unknown words are counted once each, not once per use. Essay 3 was charged
fifteen times for five words, seven of them the single word `microgrids`. One
word the dictionary lacks is one thing for a writer to resolve.

**Tone** is judged by `tools/tone_correctness.py` using the same criteria and
the same rule as `full_page_tone_quality_probe.py`: a section passes only when
target_style and usefulness are both at least 4. Scored per section, because
that is the unit a writer accepts or rejects in the HUD, and because one number
for a document hides which paragraph failed.

**The control surface** is checked by `tools/control_surface_gate.py`. It
proves every visible control is named, is enabled or explicably disabled, and
is wired to something. It does NOT press the buttons: clicking every control
opens modal dialogs that never return. So it proves a control can do something,
not that what it does is right. Controls it refuses to touch are printed with
their reason on every run.

**None of this proves the writing is good.** The rubric terms are proxies. A
gain that does not also appear on essays the rules were never tuned against is
overfitting, not a gain.

## What is deliberately not here

Commercial launch blockers - code signing, the PayPal matrix, clean-machine
install, entitlement outage - are in `docs/SALE_READINESS.md`. This file is
about whether the product works, not whether it can be sold.

`docs/WRITING_QUALITY_ACCEPTANCE.md` is superseded where it describes models.
It cites a "configured 3B model" and a 14B comparison; the product now ships
gemma4 e2b, e4b and 12b packs chosen by mode in
`src/echo_link_core/polish_hardware.py`. Its fifteen-case gate is still valid
as a narrow check and is not part of this one.
