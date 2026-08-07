

def test_the_dictionary_knows_the_forms_its_affix_rules_describe() -> None:
    """The word list was read and the affix file beside it was ignored.

    A Hunspell dictionary is a word list plus an affix file, and the flags
    after each entry's slash say which rules that entry takes. Reading only the
    list left 48,262 base forms: the speller knew "cat" but not "cats",
    "change" but not "changes", and did not know "after" at all. Every spelling
    decision inherited it - whether a word is a typo, whether a repair is
    trustworthy, and the residual-error count in the quality scorer, which
    turned out to be mostly plurals.
    """
    from echo_link_core.polish_spelling import EnglishDictionary

    dictionary = EnglishDictionary()
    dictionary.warm()

    assert dictionary.ready
    # Base forms alone came to 48,262.
    assert len(dictionary._words) > 100_000

    for word in (
        "cat", "cats", "change", "changes", "answer", "answering",
        "become", "becomes", "benefit", "benefits", "after", "alarms",
        "chores", "checking", "walked", "walking", "happiest",
    ):
        assert dictionary.contains(word), word

    # Expanding the rules must not start accepting misspellings.
    for word in ("cair", "pett", "esay", "wehre", "teh", "animul", "somewwhere"):
        assert not dictionary.contains(word), word


def test_affix_conditions_are_honoured_rather_than_applied_blindly() -> None:
    """A rule only applies where its condition matches."""
    from echo_link_core.polish_spelling import _affix_condition_matches, _apply_affix

    assert _affix_condition_matches("[^aeiou]y", "happy", suffix=True)
    assert not _affix_condition_matches("[^aeiou]y", "play", suffix=True)
    assert _affix_condition_matches(".", "anything", suffix=True)

    # "happy" + SFX (strip "y", add "iest") where the stem ends consonant + y.
    assert _apply_affix("happy", "SFX", "y", "iest", "[^aeiou]y") == "happiest"
    # The same rule must refuse a stem it does not fit.
    assert _apply_affix("play", "SFX", "y", "iest", "[^aeiou]y") == ""
    # Prefixes attach to the front.
    assert _apply_affix("check", "PFX", "", "re", ".") == "recheck"
