"""Rules the hand-added lexicon has to keep obeying.

`_LEXICON_ADDITIONS` was added on 2026-08-29 at Curtis's explicit instruction,
against a standing rule in CLAUDE.md that forbids adding a word because a corpus
essay contains it. A hand-maintained word list is the easiest place in this
codebase to hide a corpus score, so it gets the tests the rule would otherwise
have provided.

None of these assert WHICH words are in the list. That would freeze it and make
every future addition a test edit, which is how a list stops being reviewed.
They assert the properties that make it a lexicon rather than a scorecard.
"""
from __future__ import annotations

import pytest


def test_no_entry_is_a_misspelling_the_speller_would_otherwise_repair():
    """A typo in here is worse than useless: it SUPPRESSES its own correction.

    `contains` short-circuits everything downstream, so a wrong word listed as
    English is never offered a repair by any later layer. "serreous" was the
    live temptation - it is two edits from "serious" and sat in the corpus
    unfixed - and it belongs in SPELLING_CORRECTIONS instead.
    """
    from echo_link_core.polish_spelling import _LEXICON_ADDITIONS
    from echo_link_core.writing_suggestions import SPELLING_CORRECTIONS

    overlap = sorted(set(_LEXICON_ADDITIONS) & set(SPELLING_CORRECTIONS))
    assert not overlap, (
        f"these are listed as English AND as misspellings: {overlap}. "
        f"A word cannot be both; the lexicon entry wins and silently kills "
        f"the correction."
    )


def test_every_entry_is_lowercase_and_plausible():
    """Shape only, but it catches the two ways this list gets corrupted.

    An entry with a capital never matches, because `contains` casefolds before
    looking - so a capitalised word would be dead weight that reads as coverage.
    An entry with a space is a phrase, and the word scanner never produces one,
    so that is dead weight too.
    """
    from echo_link_core.polish_spelling import _LEXICON_ADDITIONS

    for word in _LEXICON_ADDITIONS:
        assert word == word.casefold(), f"{word} would never match: contains() casefolds"
        assert " " not in word, f"{word} is a phrase; the word scanner never yields one"
        assert len(word) >= 3, f"{word} is too short to be safe"


def test_the_list_is_wider_than_the_gated_corpus():
    """A lexicon helps the next document. A scorecard helps only this one.

    The four words that drove this change - cyber, lidar, lahar, outplanting -
    came from four gated essays. If the list held only what those essays needed,
    the next writer with a different technical word would get nothing, and the
    corpus score would have improved while the product did not.

    The threshold is deliberately loose. It is asking "is this a vocabulary or a
    lookup table", not policing a size.
    """
    from echo_link_core.polish_spelling import _LEXICON_ADDITIONS

    reached_through_the_corpus = {"cyber", "lidar", "lahar", "outplanting", "passivation"}
    beyond = set(_LEXICON_ADDITIONS) - reached_through_the_corpus
    assert len(beyond) >= 2 * len(reached_through_the_corpus), (
        f"only {len(beyond)} entries were not reached through the gated corpus. "
        f"This has become a scorecard for those essays rather than a lexicon."
    )


@pytest.mark.parametrize(
    "word",
    ["cair", "esay", "chroes", "teh", "recieve", "seperate", "definately"],
)
def test_known_misspellings_are_still_not_english(word):
    """The guard that would fail loudly if someone pasted a wordlist in here.

    These are misspellings this repository has specific, recorded decisions
    about. If a future bulk addition swallows any of them, every correction
    behind it dies silently and the only symptom is a slightly better score.
    """
    from echo_link_core.polish_spelling import _LEXICON_ADDITIONS

    assert word not in _LEXICON_ADDITIONS


def test_additions_are_actually_reachable_through_the_dictionary():
    """The list has to be wired in, not merely defined.

    A constant nobody reads is the failure mode this whole session has hit
    twice - the AutoResearch status panel was written, tested and never called,
    and a queue anchor pointed at the wrong one of two identical lines. Both
    looked correct in review.
    """
    from echo_link_core.polish_spelling import _LEXICON_ADDITIONS, EnglishDictionary

    dictionary = EnglishDictionary()
    for word in sorted(_LEXICON_ADDITIONS)[:5]:
        assert dictionary.contains(word), f"{word} is in the list but contains() says no"
        assert dictionary.contains(word.upper()), f"{word} must match regardless of case"
