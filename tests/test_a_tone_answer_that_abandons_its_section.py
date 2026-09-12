"""A tone answer that threw the paragraph away must never reach the document.

Measured on the release acceptance tier of 2026-09-11, by reading the output
.docx files rather than any score: the document's own title appeared repeatedly
in place of the writer's paragraph. Essay 18, Shorten, sections 9 to 13 are a
consecutive run of it:

    section  9:  78 words -> "Artificial Intelligence in Education. Artificial
                              Intelligence in Education."
    section 10: 118 words -> "Artificial Intelligence in Education. Artificial
                              Intelligence in Education."
    section 11: 101 words -> "Artificial Intelligence in Education."

122 of the 1,671 section pairs across the six tone runs came back like that, in
four of the six tones. Clear and Formal produced none.

The model did not produce those answers. The app handed a background section
whatever correction was on the HUD card, which during a prefetch burst is the
title; that cause is fixed at its source and pinned by
test_a_background_section_carries_its_own_correction_not_the_cards in
test_rewrite_palette.py.

This guard stays because it is what would have CAUGHT it, whatever produced it,
and because nothing else does. Two separate reasons, both tested here.

ONE. `split_tone_batch` counts the blocks in the answer and never reads them. An
answer holding the right number of wrong paragraphs was accepted and each block
written over a section. Its docstring promised "the worst case of a merged
answer is today's behaviour, never a lost paragraph break" - there was a worse
case it did not consider.

TWO. That check is only reached when a request carries more than one section
(`rewrite_palette.py`, `if len(members) > 1`). A section long enough to carry a
tone alone is sent as a batch of one and never reaches it, so its answer went
into the card unchecked. 30 of the 59 damaged batches were that shape.

The threshold is not fitted to this data. Across the 1,671 pairs, sections the
tones rewrote properly lost a median 0.026 and at most 0.3438 of their content
words; the abandoned ones lost at least 0.6567. Nothing lies between, so every
threshold from 0.35 to 0.65 gives the same perfect split and 0.5 is its
midpoint. Held out and used for none of that: 655 sections from the four
proofread-only batches, of which none would be refused.

A LENGTH ratio was tried first, and the reason it was rejected was itself wrong
the first time round - worth recording, because the wrong reason is the
plausible one. It looked as though length could not separate at all; that came
from pairing essay 16 by paragraph index after a separate defect had merged two
of its paragraphs, so each later section was being compared against its
neighbour's rewrite. Paired cleanly, length does separate for sources of 20
words or more. It is still not what this guard uses: across all sizes length
overlaps, because two damaged answers came back LONGER than their source.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from echo_link_app.review_spans import (  # noqa: E402
    ANSWER_MAX_LOST_CONTENT_SHARE,
    answer_abandons_the_source,
    split_tone_batch,
)

# Essay 18 of the error-riddled corpus, section 10, exactly as the harness
# captured it and exactly what came back under Improve, Rephrase and Shorten.
ESSAY_18_SECTION = (
    "Over the longer term, Teachers rmain reponsible for relationships, "
    "motivation, context, safety curriculum judgment?? and recognizing when a "
    "students difficulty is not what a model predicts. A durable program must "
    "therefore specify who reviews automated judgments, how appeals work, and "
    "what evidence is retained when a decision is contested."
)
ESSAY_18_ECHO = (
    "Artificial Intelligence in Education. Artificial Intelligence in Education."
)

# Essay 1, Friendly. The same collapse with a different target: a paraphrase of
# the title rather than the title itself. Included because a rule written only
# against the literal title would pass this and still lose the paragraph.
ESSAY_1_SECTION = (
    "Another dimension appears when, Native bees often nest alone and depend on "
    "particular flowers or nesting materials, so adding honey bee colonies can "
    "increase competition for the same forage."
)
ESSAY_1_ECHO = (
    "Let's explore urban beekeeping and how it helps the ecology of cities. "
    "Let's explore urban beekeeping and how it helps the ecology of cities."
)

# A real, aggressive, CORRECT rewrite. Shorten legitimately removes a great deal
# of text, and refusing this would gut the feature rather than protect it.
LEGITIMATE_SOURCE = (
    "From a community perspectiive,, Local ordinances commonly address setbacks, "
    "water sources, barriers, registration and hive counts, and these rules vary "
    "widely between one city and the next."
)
LEGITIMATE_SHORTENED = (
    "Local ordinances commonly address setbacks, water sources, barriers, "
    "registration and hive counts, and these rules vary widely between cities."
)


def test_the_title_echo_is_refused():
    """The exact answer that replaced essay 18's paragraph."""
    assert answer_abandons_the_source(ESSAY_18_SECTION, ESSAY_18_ECHO)


def test_a_paraphrased_title_echo_is_refused_too():
    """Essay 1 under Friendly. The words differ; the loss is the same."""
    assert answer_abandons_the_source(ESSAY_1_SECTION, ESSAY_1_ECHO)


def test_an_aggressive_but_faithful_rewrite_is_kept():
    """The expensive error is refusing good work, not accepting it."""
    assert not answer_abandons_the_source(LEGITIMATE_SOURCE, LEGITIMATE_SHORTENED)


def test_an_ordinary_proofread_is_kept():
    source = "The comittee reviewed the buget and aproved it on Tuseday."
    answer = "The committee reviewed the budget and approved it on Tuesday."

    assert not answer_abandons_the_source(source, answer)


def test_a_heading_is_too_short_to_judge():
    """Below four content words the ratio stops meaning anything.

    One substituted word in a three word heading already crosses any threshold,
    so a legitimate rewrite would be refused on arithmetic rather than evidence.
    """
    assert not answer_abandons_the_source("Overview", "Here is the overview")
    assert not answer_abandons_the_source("Next steps", "What happens next")


def test_an_empty_answer_is_left_to_its_own_branch():
    """show_sentence_result already handles an empty answer, and says so."""
    assert not answer_abandons_the_source(ESSAY_18_SECTION, "")
    assert not answer_abandons_the_source(ESSAY_18_SECTION, "   ")


def test_the_threshold_sits_in_the_measured_gap():
    """Guard the constant itself.

    The gap measured across 1,671 pairs runs from 0.3438 (the most aggressive
    correct rewrite) to 0.6567 (the mildest real damage). A future edit that
    moves this outside that band is choosing a side of the trade-off and should
    have to say so here.
    """
    assert 0.3438 < ANSWER_MAX_LOST_CONTENT_SHARE < 0.6567


def test_a_batch_of_title_echoes_is_refused_as_a_batch():
    """Count alone accepts this answer, which is how it reached the document.

    split_tone_batch still returns the blocks - its job is splitting, and the
    caller decides. This pins the behaviour that made the damage possible so
    that anyone moving the content check knows the count check does not do it.
    """
    answer = "\n\n".join([ESSAY_18_ECHO] * 3)

    parts = split_tone_batch(answer, 3)

    assert parts is not None and len(parts) == 3
    # ... and every one of them abandons the section it was meant to rewrite.
    assert all(answer_abandons_the_source(ESSAY_18_SECTION, part) for part in parts)


def test_a_section_is_judged_against_its_own_source():
    """Not against the batch, and not against the document.

    The check has only the answer and the one section it belongs to. A block
    that faithfully rewrites a DIFFERENT section must still be refused for this
    one, or a reordered answer would slip through.
    """
    faithful_but_wrong_section = LEGITIMATE_SHORTENED

    assert answer_abandons_the_source(ESSAY_18_SECTION, faithful_but_wrong_section)
