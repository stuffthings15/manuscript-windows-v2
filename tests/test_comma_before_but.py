"""A comma never follows a full stop, however the section was captured.

The last punctuation artefact in the 20-essay corpus was "other equipment.,
but the animal creates natural entertainment". The rule that joins two clauses
with ", but" excluded a preceding comma, semicolon and colon from its
lookbehind, but not a sentence terminator, so a section that had already ended
gained a comma after its full stop.
"""

import pytest

from echo_link_core.polish_proofread import _apply_fast_proofread_rules


@pytest.mark.parametrize(
    "source",
    (
        ". But the animal creates natural entertainment",
        "The dog barks. But the cat is quiet.",
        "The habitat is clean! But the water needs changing.",
        "Is the pet fed? But the water needs changing.",
    ),
)
def test_a_comma_is_never_added_after_a_sentence_ends(source: str) -> None:
    assert ".," not in _apply_fast_proofread_rules(source)
    assert "!," not in _apply_fast_proofread_rules(source)
    assert "?," not in _apply_fast_proofread_rules(source)


def test_the_comma_is_still_added_between_two_clauses() -> None:
    # The rule earns its place mid-sentence, and this is what it is for.
    assert _apply_fast_proofread_rules("The pet is calm but the animal needs care") == (
        "The pet is calm, but the animal needs care."
    )
