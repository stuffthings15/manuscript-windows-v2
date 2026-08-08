

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


def _repairs(text: str):
    """What the deterministic speller would change, as original -> replacement."""
    from echo_link_core.polish_spelling import PolishRuleEngine

    engine = PolishRuleEngine()
    engine.warm()
    return {s.original.casefold(): s.replacement for s in engine.analyze_spelling(text)}


def test_the_measured_misspellings_are_repaired():
    """Each appeared in 13 to 20 of the 20 corpus essays after the speller ran."""
    cases = {
        "proper cair": ("cair", "care"),
        "pay atenshun": ("atenshun", "attention"),
        "becuz of rain": ("becuz", "because"),
        "the oner left": ("oner", "owner"),
        "probly true": ("probly", "probably"),
        "agin and agin": ("agin", "again"),
        "sumtimes it rains": ("sumtimes", "sometimes"),
        "an edjucashunal toy": ("edjucashunal", "educational"),
        "not avalible": ("avalible", "available"),
        "buying equipments": ("equipments", "equipment"),
        "even worser": ("worser", "worse"),
    }
    for text, (word, expected) in cases.items():
        assert _repairs(text).get(word) == expected, text


def test_the_speller_no_longer_turns_school_into_a_drinking_toast():
    """Measured: "skool" resolved to "skol", a Nordic toast, in 13 essays."""
    assert _repairs("go to skool")["skool"] == "school"


def test_british_forms_are_americanised():
    """Both the misspelling and the correct British spelling land on "learned"."""
    assert _repairs("she lernt it")["lernt"] == "learned"
    assert _repairs("she learnt it")["learnt"] == "learned"


def test_wether_becomes_whether_only_as_a_conjunction():
    assert _repairs("wether or not it rains")["wether"] == "whether"
    assert "wether" not in _repairs("the wether was sheared")
    assert "wether" not in _repairs("a wether in the field")


def test_aint_expands_to_match_its_subject():
    assert _repairs("it aint ready")["aint"] == "is not"
    assert _repairs("they aint ready")["aint"] == "are not"
    assert _repairs("I aint ready")["aint"] == "am not"
    assert _repairs("forgetting aint good")["aint"] == "is not"


def test_aint_is_left_alone_when_the_subject_is_unclear():
    """Writing the wrong verb is worse than leaving the word for a reader."""
    assert "aint" not in _repairs("the dogs aint ready")
    assert "aint" not in _repairs("a king aint here")
