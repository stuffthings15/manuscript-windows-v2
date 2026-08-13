"""Homophones are correctly spelled words in the wrong place.

Neither SymSpell nor Hunspell can see them, so every one of these survived the
graded corpus run untouched. Each test names the sentence class, not one essay
sentence: the rules key on the neighbouring word class, and the pairs below are
the two readings the rule has to tell apart.
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
    (
        "In conclusion a pigeon can make a grate pet for a gardener.",
        "In conclusion a pigeon can make a great pet for a gardener.",
    ),
    (
        "It also requires common sense, witch is a lot of requirements.",
        "It also requires common sense, which is a lot of requirements.",
    ),
    (
        "The animal cud become an important part of the home.",
        "The animal could become an important part of the home.",
    ),
    (
        "But wit correct preparation the animal is fine.",
        "But with correct preparation the animal is fine.",
    ),
    (
        "A reason to care bout something beyond yourself.",
        "A reason to care about something beyond yourself.",
    ),
    (
        "That is why there a good choice in this scenario.",
        "That is why they're a good choice in this scenario.",
    ),
    (
        "Nobody should get one just because they read a essay.",
        "Nobody should get one just because they read an essay.",
    ),
    ("Your going to regret that.", "You're going to regret that."),
    ("Its a long way from here.", "It's a long way from here."),
    ("The dog is bigger then the cat.", "The dog is bigger than the cat."),
    ("The main affect was obvious.", "The main effect was obvious."),
    ("This will effect the outcome.", "This will affect the outcome."),
    ("You will loose the paper.", "You will lose the paper."),
)

UNTOUCHED = (
    # The reading in which the written word is the correct one. These are the
    # safety property: a rule that fires here is corrupting the author's text,
    # which is worse than leaving the homophone alone.
    "Clean the grate today before the fire is lit.",
    "The witch in the story was frightening.",
    "The cattle chewed the cud in the field.",
    "Her dry wit carried the room.",
    "He won a bout of the tournament.",
    "Is there a good choice here?",
    "She read a university report about a one-way street.",
    "A European delegate arrived.",
    "Your essay is finished.",
    "The dog wagged its tail.",
    "We ate, then we left.",
    "The drug did not affect him.",
    "The effect was small.",
    "The screw was loose.",
    "We were then told to wait.",
)


@pytest.mark.parametrize(("source", "expected"), CORRECTED)
def test_the_wrong_word_is_replaced(source: str, expected: str) -> None:
    assert _corrected(source) == expected


@pytest.mark.parametrize("source", UNTOUCHED)
def test_the_right_word_is_left_alone(source: str) -> None:
    assert _corrected(source) == source


def test_capitalisation_of_the_original_word_is_kept() -> None:
    assert _corrected("Grate pets need care.") == "Great pets need care."
    assert _corrected("Cud you help?") == "Could you help?"
