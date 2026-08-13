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


CORRECTED = (
    ("Therefore pigeons makes good pets.", "Therefore pigeons make good pets."),
    (
        "A pet can only be great when its needs is met.",
        "A pet can only be great when its needs are met.",
    ),
    (
        "Some habitats does need lights and heaters.",
        "Some habitats do need lights and heaters.",
    ),
    ("The turtles is a patient pet.", "The turtles are a patient pet."),
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
    # Singular nouns that end in s. A rule keyed on the ending alone rewrites
    # the verb after every one of these.
    "The bus is late.",
    "Her analysis is finished.",
    "The business has three owners.",
    "Physics is hard.",
    "The news was good.",
    "This is the plan.",
    "The species is protected.",
    "She is a witness.",
    "The class was quiet.",
    # The subject is "one"; the plural is inside a prepositional phrase.
    "One of the dogs is friendly.",
    # The word after the -s noun is another noun, not a verb.
    "A gecko got habits needs and a personality.",
    "Daily life sometimes gets boring.",
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


def test_a_doubled_word_is_not_read_as_an_agreement_error() -> None:
    # "behavior makes makes a schedule" is a duplicate, and rewriting the
    # second one to "make" would hide the defect the duplicate rule owns.
    assert "makes make" not in _corrected("Checking its behavior makes makes a schedule.")
