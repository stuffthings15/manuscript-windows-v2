"""The grader is measured too, because a broken rubric aims the product wrong.

Coherence charged six sentences of correct writing across the three held-out
essays, and every one of those points came from wording memorised out of the
twenty-essay corpus. A rule tuned to satisfy that rubric would have learned to
delete good sentences, which is the damage the narrowed clause removal in
`fbc38c0` was written to undo.

The two groups below are the whole test: writing that must never be charged,
and the defects that must still be caught.
"""

import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from grade_corpus import (  # noqa: E402
    META_COMMENTARY,
    _confusable_hits,
    _run_on_sentences,
)

# Ordinary sentences from the held-out essays. Each was charged 2.5 points.
CORRECT_WRITING = (
    # A bare "it" is not a reference to the writing.
    "It does not take much staff to keep that going, although the heating bill "
    "does climb a little.",
    "It is not a movie, and it does not need batteries, although some habitats "
    "do need lights, heaters, filters, or other equipment.",
    "Watching the cat move, eat, rest, or investigate something gives the owner "
    "a small event to pay attention to, and it does not need a screen.",
    # "still counts" is ordinary English with a subject that is not the writing.
    "If a kid reads comics all afternoon it still counts as reading, and nobody "
    "at the desk makes a face about it.",
    "The strip of grass along the north fence is only about ten feet wide, but "
    "it still counts.",
    # A thesis statement belongs in the essay, and "works" is not a judgement.
    "This essay is about a tour guide who works early mornings.",
    # The self-referential noun is doing ordinary work here.
    "This part of the yard does not get any sun after two in the afternoon.",
)

# The planted defects, from the corpus and from held-out essay 3.
JUDGES_THE_WRITING = (
    "This part does not connect much, but it still counts.",
    "This paragraph should probably be about fun, but responsibility comes back "
    "again.",
    "However, this essay is mostly saying that pets are good, and now it "
    "suddenly sounds like instructions.",
    "I am not sure this paragraph fits with the one before it but I already "
    "typed it out.",
    # Asserted and taken back before the full stop, with nothing in between.
    "This is why it is good but also not.",
    "Which is why it is good but also not.",
)


@pytest.mark.parametrize("sentence", CORRECT_WRITING)
def test_ordinary_writing_is_not_charged_as_incoherence(sentence: str) -> None:
    assert META_COMMENTARY.search(sentence) is None


@pytest.mark.parametrize("sentence", JUDGES_THE_WRITING)
def test_a_sentence_that_judges_the_writing_is_charged(sentence: str) -> None:
    assert META_COMMENTARY.search(sentence) is not None


# Confusable words the speller cannot see, because both spellings are real.
# The first three are live misses in the held-out essays, scored 15 of 15 for
# homophones by the rubric that asked the correction rules what they would
# change.
CONFUSED = (
    ("My cousin reads with an app on hers because the print is to small for her.", "too"),
    ("If a teacher catches you with it out while your putting it away it counts.", "you're"),
    ("Two of the screws on the swing set frame are lose and one bolt is missing.", "loose"),
    ("I do not want to loose my place in the queue.", "lose"),
    ("The new bridge is wider then the one it replaced.", "than"),
)

# Every one of these was charged by a version of the rules that was too wide.
CORRECT_USE = (
    "The doors are open from noon to five, and anybody can walk in.",
    "The researchers worked together to correct the problem before the deadline.",
    "We moved to New York last year and the rent has not stopped climbing.",
    "She put the leftover paint to good use on the shed door.",
    "He fell asleep listening to loud music with the window open.",
    "Its best feature is the way the roof folds back in under a minute.",
    "The council will lose the grant if the forms are late.",
    "Back then the fence was still standing.",
)


@pytest.mark.parametrize(("sentence", "meant"), CONFUSED)
def test_a_confusable_word_in_an_impossible_position_is_found(
    sentence: str, meant: str
) -> None:
    hits = _confusable_hits(sentence)
    assert hits, f"nothing found in {sentence!r}"
    assert any(hit.endswith(meant) for hit in hits), hits


@pytest.mark.parametrize("sentence", CORRECT_USE)
def test_a_confusable_word_used_correctly_is_left_alone(sentence: str) -> None:
    assert _confusable_hits(sentence) == []


def test_clauses_joined_with_no_punctuation_are_a_run_on() -> None:
    source = [
        "Almost everybody in my grade owns one and they bring it to school "
        "anyway so the argument is not really about the devices it is about "
        "what we do with them."
    ]
    assert _run_on_sentences(source) == source


def test_a_long_sentence_that_is_punctuated_is_not_a_run_on() -> None:
    # Length is not the defect. This is the same clause count, written properly.
    source = [
        "Almost everybody in my grade owns one, and they bring it to school "
        "anyway, so the argument is not really about the devices, it is about "
        "what we do with them."
    ]
    assert _run_on_sentences(source) == []


def test_a_long_single_clause_is_not_a_run_on() -> None:
    # No coordinator, so there is no second clause to have punctuated. Merely
    # long is not the same defect, and the 45-word rule already charges it.
    source = [
        "The enormous weathered wooden climbing frame at the far northern end "
        "of the recreation ground near the allotment gates was repainted again "
        "last autumn by the parish maintenance team."
    ]
    assert _run_on_sentences(source) == []


def test_no_corpus_wording_is_memorised_in_the_pattern() -> None:
    """The strings that made the rubric flatter the essays it was written beside.

    Each one marked unseen writing wrong. They are named here so a later session
    cannot quietly put them back to make a corpus number move.
    """
    for memorised in (
        "still counts",
        "good but also not",
        "responsibility",
        "two reasons at once",
        "maybe the same reason",
        "instructions",
    ):
        assert memorised not in META_COMMENTARY.pattern
