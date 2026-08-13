"""Punctuation that marks where one unit of meaning ends and the next begins.

The graded corpus output left a 90-word conclusion with no internal commas.
This rule trims a doubled full stop. Punctuating a bare list and splitting a
run-on are both deliberately absent; see the notes in writing_suggestions.
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
        "That is a lot of requirements.. Most things have requirements.",
        "That is a lot of requirements. Most things have requirements.",
    ),
    ("He waited....then he left.", "He waited...then he left."),
)

UNTOUCHED = (
    # A list that is already punctuated.
    "She packed apples, oranges, bananas, and water.",
    # A list with multi-word items, whose boundaries this layer cannot find.
    "The group counted volunteer hours crop donations and meeting attendance.",
)


def test_a_deliberate_ellipsis_is_left_alone() -> None:
    # Only the three dots are asserted. The capital after them comes from the
    # existing sentence-start rule, which treats an ellipsis as a terminator;
    # that predates these rules and is out of scope here.
    assert "..." in _corrected("He paused ... and then continued.")


@pytest.mark.parametrize(("source", "expected"), CORRECTED)
def test_the_boundary_is_marked(source: str, expected: str) -> None:
    assert _corrected(source) == expected


@pytest.mark.parametrize("source", UNTOUCHED)
def test_correct_punctuation_is_left_alone(source: str) -> None:
    assert _corrected(source) == source


def test_a_bare_list_is_left_for_a_layer_that_can_find_item_boundaries() -> None:
    # A rule for this was written and withdrawn on 2026-08-13: it read
    # "volunteer hours crop donations" as four items and put a comma inside
    # each one. A missing comma is recoverable; a comma inside a noun phrase
    # is not. See the note in writing_suggestions.py.
    source = "Instead, the group counted volunteer hours crop donations and attendance."
    assert _corrected(source) == source
