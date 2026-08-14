"""Subject-verb agreement, and dictation filler that landed inside a phrase.

Agreement was the clearest grammar error left in the graded corpus output, and
the one a teacher circles first. The filler rule is the only rule in this layer
that deletes the author's words, so it fires only where the phrase is
syntactically impossible.
"""

import pytest

from echo_link_core.writing_suggestions import (
    WritingSuggestionEngine,
    apply_all_suggestions,
)


def _corrected(text: str) -> str:
    engine = WritingSuggestionEngine()
    return apply_all_suggestions(text, engine.analyze(text))


# The plural-subject cases moved to tests/test_polish_syntax.py with the rule
# itself, which now asks the word-class table instead of four hand-written
# lists. The pronoun rule stays here: it needs no lexicon, so it can live in
# the module that must not do I/O.
CORRECTED = (
    ("They was late again.", "They were late again."),
    ("We is ready now.", "We are ready now."),
    ("People has different opinions.", "People have different opinions."),
    (
        "The animal can belong in a basically home.",
        "The animal can belong in a home.",
    ),
    (
        "The you know animal became part of the day.",
        "The animal became part of the day.",
    ),
    (
        "It mainly wants the you know correct environment.",
        "It mainly wants the correct environment.",
    ),
    (
        # Two rules meeting on one phrase. Correcting the determiner to "an"
        # before deleting the filler left "an good choice" behind.
        "That is why there a or whatever good choice.",
        "That is why they're a good choice.",
    ),
)

UNTOUCHED = (
    # Real adverbs, outside a noun phrase, which the author meant.
    "The essay is basically finished.",
    "He was, to be honest, exhausted.",
    "The dog is loyal.",
)


@pytest.mark.parametrize(("source", "expected"), CORRECTED)
def test_the_defect_is_corrected(source: str, expected: str) -> None:
    assert _corrected(source) == expected


@pytest.mark.parametrize("source", UNTOUCHED)
def test_correct_writing_is_left_alone(source: str) -> None:
    assert _corrected(source) == source


