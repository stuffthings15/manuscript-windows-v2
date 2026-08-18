"""The second look at words the speller refused to guess.

Every case here is a word measured surviving into a finished document on
2026-08-17, and the acceptance rules exist because this runs after a sentence
has already been corrected once. A second pass that can reword is a second pass
that can undo the first.
"""

from __future__ import annotations

from echo_link_core.polish_residual import (
    accept_residual_repair,
    residual_unknown_words,
)


class _Dictionary:
    """The words this test pretends to know."""

    def __init__(self, words):
        self._words = {word.casefold() for word in words}

    def contains(self, word: str) -> bool:
        return word.casefold() in self._words


KNOWN = _Dictionary(
    [
        "it", "just", "needs", "to", "fit", "the", "person", "and", "place",
        "essay", "is", "about", "a", "dog", "cafe", "walking", "prison", "in",
    ]
)


def test_the_words_the_speller_refused_are_found():
    text = "it just nees to fit the prson and the place"

    assert residual_unknown_words(text, KNOWN) == ("nees", "prson")


def test_a_name_is_not_residue():
    """Arequipa, Kisumu and Dlaat are all unknown and only one is a mistake.

    A corrector that rewrites names does more damage than the typo it fixes,
    so capitalised words are never offered for repair.
    """
    text = "the dog is in Arequipa and Kisumu"

    assert residual_unknown_words(text, KNOWN) == ()


def test_a_correct_word_the_dictionary_spells_plainly_is_not_residue():
    assert residual_unknown_words("the cafe is a dog", KNOWN) == ()


def test_a_spelling_only_repair_is_accepted():
    original = "it just nees to fit the prson and the place"
    candidate = "it just needs to fit the person and the place"

    assert accept_residual_repair(original, candidate, KNOWN) == candidate


def test_a_reworded_sentence_is_refused_even_if_the_spelling_is_fixed():
    """The model returning a better sentence is still refused.

    This runs after the sentence has already been corrected and validated, so
    anything beyond a spelling swap is a second rewrite of text that was
    already agreed.
    """
    original = "it just nees to fit the prson and the place"
    candidate = "it needs to fit the person and the place well"

    assert accept_residual_repair(original, candidate, KNOWN) is None


def test_changing_a_word_that_was_already_correct_is_refused():
    original = "it just nees to fit the prson and the place"
    candidate = "it just needs to fit the person and the essay"

    assert accept_residual_repair(original, candidate, KNOWN) is None


def test_swapping_one_unknown_word_for_another_is_refused():
    original = "it just nees to fit the prson and the place"
    candidate = "it just needs to fit the prsn and the place"

    assert accept_residual_repair(original, candidate, KNOWN) is None


def test_an_unchanged_answer_is_not_a_repair():
    original = "it just nees to fit the prson and the place"

    assert accept_residual_repair(original, original, KNOWN) is None


def test_an_empty_answer_is_refused():
    assert accept_residual_repair("it just nees to fit", "   ", KNOWN) is None
