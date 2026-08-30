"""A capitalised word gets the stricter tie-break bar.

Lowering `_DOMINANT_FREQUENCY_RATIO` from 50 to 30 on 2026-08-29 bought two
points on essay 10 and unfroze a name on the way: "Csco" sits one edit from
cisco at 27.9M and cusco at 627K, a 44.5x lead that is a tie at 50x and decisive
at 30x, so the corrector began writing "Cisco". Cusco is a city in Peru with an
essay in this repository's own corpus.

The costs are different in kind, which is why one ratio cannot serve both. A
mangled common word is a typo a reader forgives. A rewritten name is the product
putting words in someone's mouth, and the writer may never notice.

test_names_the_speller_cannot_place_are_still_frozen in test_polish_engine.py
caught the regression and these tests pin the mechanism, so a future change to
either ratio fails here with a message about names rather than somewhere distant
with a message about entities.
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def dictionary():
    from echo_link_core.polish_spelling import EnglishDictionary

    value = EnglishDictionary()
    value.warm()
    return value


def test_a_capitalised_word_is_not_resolved_by_a_44x_lead(dictionary):
    """The exact case that regressed. 44.5x clears 30 and does not clear 50."""
    assert dictionary.suggestion("Csco") is None


def test_the_same_word_lowercase_is_still_repaired(dictionary):
    """The stricter bar must apply to NAMES, not to everything.

    Without this the fix would be indistinguishable from reverting the ratio,
    and the two points it bought on essay 10 would have been given back.
    """
    repair = dictionary.suggestion("csco")
    assert repair is not None
    assert repair[0] == "cisco"


def test_an_ordinary_lowercase_repair_still_clears_the_lower_bar(dictionary):
    """stateon -> station is what the 30x ratio was lowered for.

    station leads staten 32x, which is refused at 50 and allowed at 30. If this
    ever returns None the ratio has been reverted and essay 10 has lost a point.
    """
    repair = dictionary.suggestion("stateon")
    assert repair is not None
    assert repair[0] == "station"


def test_the_named_bar_is_stricter_than_the_ordinary_one():
    """Ordering, not values, so tuning either stays possible.

    Asserting the numbers would freeze two constants the loop is expected to
    keep measuring. Asserting the relationship is the property that matters: a
    name may never be guessed at more readily than a common word.
    """
    from echo_link_core.polish_spelling import (
        _DOMINANT_FREQUENCY_RATIO,
        _DOMINANT_FREQUENCY_RATIO_NAMED,
    )

    assert _DOMINANT_FREQUENCY_RATIO_NAMED >= _DOMINANT_FREQUENCY_RATIO


def test_the_entity_check_keeps_the_capital_it_needs():
    """The leak that made the first attempt at this fix do nothing.

    polish_entities.py asked `dictionary.suggestion(value.casefold())`, throwing
    away the very evidence the new bar reads, in the one call whose purpose is
    deciding whether a NAME is a typo. Adding the bar changed nothing until that
    was found, and any future case-sensitive speller rule hits the same wall.
    """
    import inspect

    from echo_link_core import polish_entities

    source = inspect.getsource(polish_entities)
    assert "dictionary.suggestion(value.casefold())" not in source, (
        "the entity check casefolds before asking the speller, which discards "
        "the capital that the named-word tie-break bar depends on"
    )
