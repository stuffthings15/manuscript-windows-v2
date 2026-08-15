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

from grade_corpus import META_COMMENTARY  # noqa: E402

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
