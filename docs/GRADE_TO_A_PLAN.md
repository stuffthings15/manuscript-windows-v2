# Plan: raising a corrected essay from C- to A

## The measurement this starts from

Graded `build/batch/14_Pigeons_in_Athens_Greece.docx` on 2026-08-13, the last
document the HUD corrected (10 sections, 10 accepts, 0 unknown words, 0
artefacts).

| Category | Now | Target | Reachable how |
|---|---|---|---|
| Spelling | B+ | A | Silent, existing mechanism |
| Homophones | D | A | Silent, new context rules |
| Punctuation | D+ | A- | Silent, new boundary rules |
| Grammar | C- | A- | Silent, agreement and filler rules |
| Organization | D | B+ | Proposed, requires consent |
| Coherence | F | B+ | Proposed, requires consent |
| **Overall** | **C- (68)** | **A- to A (91)** | |

## The constraint that shapes everything below

Organization and Coherence are the two lowest scores and they are the two the
correction path must never touch on its own. Moving paragraph 8 ahead of
paragraph 4 reorders the argument. Deleting "which is why it is good but also
not" removes a sentence the author wrote. Both change meaning, which is exactly
what the meaning guards exist to prevent, and a tool that silently rewrites a
student's argument is the wrong tool for a school assignment.

The resolution is not to weaken the guards. It is to add a second, clearly
separate output: **document-level observations the student reads and applies
themselves.** The guards keep their veto over anything written back to the
document automatically. Nothing in this plan edits them.

## Phase 1: homophones (silent)

Every remaining spelling error in essay 14 is a correctly spelled word in the
wrong place, invisible to both SymSpell and Hunspell. Observed: `grate/great`,
`witch/which`, `cud/could`, `wit/with`, `bout/about`, `there/they're`,
`a essay/an essay`.

These generalize honestly, because the trigger is the neighbouring word class,
not the sentence. Roughly a dozen rules in `writing_suggestions.py`:

- `grate` followed by a noun
- `witch` after a comma and before a copula
- `cud` outside a farming context
- `wit` followed by a determiner or adjective
- `bout` following a verb
- `there` followed by determiner plus noun
- `a` before a vowel sound
- plus `your/you're`, `its/it's`, `then/than`, `affect/effect`, `lose/loose`,
  the five most common in student writing that this corpus happens not to show

Each ships with its own regression test, observed failing without the rule.

Risk: false positives on the rare correct use. Mitigation: every rule requires
the neighbouring word class, never the bare word. `their/there/they're` is the
one to be most careful with; write it last, after the pattern is proven.

Gets: Homophones D -> A.

## Phase 2: sentence boundaries (silent)

The conclusion is one 90-word sentence with no internal punctuation. Two rules:

1. Three or more bare noun phrases before `and` with zero commas present. The
   existing serial-comma rule deliberately requires two commas to avoid the
   false positive documented beside it; this is the complementary case and must
   be written as a separate rule rather than by loosening that one.
2. Independent clause followed directly by another independent clause with no
   conjunction and no terminal punctuation. Split at the boundary.

Also: collapse `..` to `.`, and restore sentence-initial capitals after the
split.

Gets: Punctuation D+ -> A-.

## Phase 3: agreement and filler (silent)

- Subject-verb agreement: plural noun immediately followed by a third-person
  singular verb (`pigeons makes`). Highest-visibility grammar error left, and
  the easiest class to detect reliably.
- Dictation filler: `or whatever`, `you know`, `basically` used as a determiner
  (`a basically home`, `a basically a personality`). These are artefacts of
  speech, not authored content, so removing them does not change meaning. This
  needs an explicit carve-out written at the call site, and it only fires where
  the phrase is syntactically impossible, never where `basically` is a real
  adverb.

Gets: Grammar C- -> A-.

## Phase 4: the document review pass (proposed, never silent)

New surface, not a new correction rule. After the section pass finishes, one
document-level analysis produces a short list of observations. Each is a
sentence of plain advice with an anchor to the paragraph it refers to. None is
ever written back automatically. The student reads them and decides.

What it detects, all of which essay 14 exhibits:

- **Ordering**: a paragraph containing "the first reason" appearing after two
  paragraphs that already argue benefits. Report: "Paragraph 8 introduces your
  first reason but comes after two supporting paragraphs. Consider moving it
  ahead of paragraph 4."
- **Self-negation**: a paragraph whose final sentence contradicts the paragraph
  it closes ("which is why it is good but also not", "This part does not
  connect much"). Report the sentence and say it weakens the point rather than
  supporting it.
- **Thesis placement**: a thesis-shaped sentence appearing outside the opening
  paragraph.
- **Repetition**: the same closing formula reused across paragraphs.
- **Unsupported claim**: a paragraph asserting a benefit with no example or
  reason attached.

Where it lives: a new `polish_structure.py` in the `polish_*` family, taking the
whole document text and returning observations. No Qt, no I/O, deterministic
where it can be and model-backed only where it cannot. It imports nothing from
the correction path and the correction path imports nothing from it, so a defect
here can never alter a document.

Where it surfaces: a separate panel or a section of the assistant view titled so
it cannot be confused with corrections. No accept button that edits the document.
Advice only.

Gets: Organization D -> B+, Coherence F -> B+, and the overall from B to A-.

## Phase 5: verification

The current grade came from me reading one essay. That is not a measurement
anyone should build on.

- Grade all 20 corpus essays before and after, using a fixed rubric written down
  first, so the per-category numbers are comparable rather than impressionistic.
- The corpus essays are deliberately corrupted, which caps the realistic ceiling.
  Add two or three ordinary essays with normal errors so the grade is not
  measured only against worst-case input.
- Full suite green and ruff clean at every commit, as always.
- `tools/entity_frequency_probe.py` and `tools/dead_essay_probe.py` clean.
- One full 20-essay HUD run at the end, on request, since it takes the screen.

## What this plan will not do

- It will not touch the meaning guards.
- It will not let any structural observation write to a document.
- It will not add memorized sentences. If a rule only matches one essay's
  wording, it does not ship, and the defect is recorded instead.
- It will not claim an A until the rubric run measures one.

## Honest ceiling

Phases 1 to 3 alone reach B- to B, and they are the safe, mechanical part.
The A depends entirely on Phase 4 being genuinely useful advice that a student
acts on. If the observations are vague, the student ignores them and the grade
stays at B. That is the risk in this plan, and it is a product risk rather than
an engineering one.
