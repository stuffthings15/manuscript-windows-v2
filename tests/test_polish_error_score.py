"""The comparative scorer behind Android-style correction acceptance.

Every test here asks the only question the engine asks: does a repaired
sentence score lower than the broken one it came from?
"""

from __future__ import annotations

from echo_link_core.polish_error_score import error_score


def test_clean_prose_scores_zero() -> None:
    assert error_score("The dogs are happy. They ran to the gate.") == 0


def test_repair_scores_lower_than_the_error_it_fixed() -> None:
    assert error_score("The dogs are happy.") < error_score("They is happy")


def test_reduplicated_proper_nouns_are_not_repeated_words() -> None:
    # The rule is lowercase-only precisely so place names survive it.
    assert error_score("Bora Bora is nice.") == 0
    assert error_score("She visited Walla Walla.") == 0


def test_lowercase_repeated_word_is_an_error() -> None:
    assert error_score("This is the the gate.") > 0


def test_capitalised_repeat_is_not_counted() -> None:
    """Deliberate: the same leniency that protects "Bora Bora" also spares
    a capitalised "The the". Android accepts that trade and so do we."""
    assert error_score("The the gate is shut.") == 0


def test_possessive_after_an_article_is_not_a_plural() -> None:
    assert error_score("A dog's bowl.") == 0


def test_missing_terminal_punctuation_counts_once() -> None:
    assert error_score("The dogs are happy") == 1
    assert error_score("The dogs are happy.") == 0


def test_heading_needs_no_full_stop() -> None:
    assert error_score("# Dogs in Bakersfield") == 0


def test_single_letter_lowercase_sentence_start_counts() -> None:
    """Android writes this rule as ``[a-z]\\b``, which is one lowercase letter
    followed by a boundary, so it catches a stray "i" opening a sentence and
    not a lowercase word. Pinned here so a later "tidy-up" of the pattern is a
    deliberate change to the score rather than an accident."""
    assert error_score("The gate is shut. i locked it.") > error_score(
        "The gate is shut. I locked it."
    )


def test_space_before_punctuation_counts() -> None:
    assert error_score("The dogs are happy .") > 0


def test_score_never_goes_negative() -> None:
    # Possessives are subtracted, so a text of nothing else must floor at zero.
    assert error_score("A dog's bowl. An owner's leash.") == 0


def test_counts_accumulate_rather_than_saturate() -> None:
    """A magnitude, not a flag - this is what makes <= meaningful."""
    one = error_score("This is the the gate.")
    two = error_score("This is the the gate. Here is the the door.")
    assert two > one


def test_speller_prefers_the_shape_of_the_mistake_over_frequency() -> None:
    """Ranking equally close candidates by frequency picked brands over words.

    "esay" sat one edit from both "essay" and "ebay", and the brand won. A
    missing letter and a collapsed double are what people actually type, so
    those are preferred first and frequency only breaks the remaining ties.

    "esay" no longer resolves here at all, and that is the fix rather than a
    regression. Once transpositions were recognised as ordinary typing, "easy"
    joined "essay" as an equally well-shaped repair, and frequency prefers
    "easy" by ten times - so the speller would have picked the wrong one. It
    now declines to guess between two equal candidates and leaves the word for
    the model, which reads the sentence. Verified live: "i had to write a esay
    about my pett dog ... poor cair" comes back "I had to write an essay about
    my pet dog ... poor care", where the speller alone would have written
    "easy" and "chair".
    """
    from echo_link_core.polish_spelling import PolishRuleEngine

    engine = PolishRuleEngine()
    text = "the adress is wrong in esay 1 with a pett"
    repairs = {
        text[value.start : value.end]: value.replacement
        for value in engine.analyze_spelling(text)
    }
    assert "esay" not in repairs
    # A collapsed double, not the more frequent "petty".
    assert repairs.get("pett") == "pet"
    # The deleted letter must be half of a doubled pair: "adress" drops a
    # leading "a" to reach "dress", and the unrelated "ss" must not excuse it.
    assert repairs.get("adress") == "address"


def test_capitalised_typos_repair_but_names_are_left_alone() -> None:
    """A capitalised word is not automatically a name.

    Refusing every capitalised word left "Ansering these questions" and the
    title "Dogs Makes Grate Petts" uncorrected through whole essays - the two
    places a reader looks first. The protection now turns on the shape of the
    edit: a name and its neighbour differ by a substitution or a plain
    deletion, a typo by a missing letter or a collapsed double.
    """
    from echo_link_core.polish_spelling import PolishRuleEngine

    engine = PolishRuleEngine()

    def repairs(text: str) -> dict[str, str]:
        return {
            text[value.start : value.end]: value.replacement
            for value in engine.analyze_spelling(text)
        }

    # Missing letter, and a collapsed double: both repaired.
    assert repairs("Ansering these questions").get("Ansering") == "Answering"
    assert repairs("Dogs Makes Grate Petts").get("Petts") == "Pets"
    assert repairs("Warrsaw is cold").get("Warrsaw") == "Warsaw"
    # A person is not a country. Plain deletion stays refused.
    assert "Malik" not in repairs("Malik went home")


def test_speller_reads_a_transposition_as_a_typing_slip() -> None:
    """Two swapped letters had no category, so they lost to worse candidates.

    `_typo_shape_rank` knew a collapsed double and a dropped letter and nothing
    else, so every transposition fell into the catch-all bucket beside a plain
    substitution and frequency decided. "wehre" became "were" instead of
    "where" and "chroes" became "chromes" instead of "chores" - both wrong
    words, inserted before the model ever saw the sentence.
    """
    from echo_link_core.polish_spelling import EnglishDictionary

    dictionary = EnglishDictionary()
    dictionary.warm()
    for word, expected in (
        ("wehre", "where"),
        ("chroes", "chores"),
        ("recieve", "receive"),
        ("rqeuirements", "requirements"),
    ):
        suggestion = dictionary.suggestion(word)
        assert suggestion is not None, word
        assert suggestion[0] == expected


def test_speller_declines_to_guess_between_two_equal_repairs() -> None:
    """A wrong substitution is unrecoverable, so an even choice is refused.

    The model never sees the original word - it is handed the speller's answer
    - so guessing "chair" for "cair" bakes the wrong word into the sentence.
    Where two candidates share a distance and a shape and frequency does not
    separate them decisively, no substitution is offered and the misspelling
    reaches the model with its sentence intact.
    """
    from echo_link_core.polish_spelling import EnglishDictionary

    dictionary = EnglishDictionary()
    dictionary.warm()
    assert dictionary.suggestion("cair") is None
    assert dictionary.suggestion("esay") is None


def test_a_dominant_word_still_wins_an_otherwise_even_choice() -> None:
    """Refusing every tie would leave obvious typos on screen.

    "the" outweighs "tech" by 250 times and "animal" outweighs "animus" by
    300. Those are not guesses. Only near neighbours in frequency count as a
    real tie.
    """
    from echo_link_core.polish_spelling import EnglishDictionary

    dictionary = EnglishDictionary()
    dictionary.warm()
    for word, expected in (("teh", "the"), ("animul", "animal")):
        suggestion = dictionary.suggestion(word)
        assert suggestion is not None, word
        assert suggestion[0] == expected


def test_the_frequency_dictionary_is_read_without_its_byte_order_mark() -> None:
    """The most common word in English was unreachable as a correction.

    The bundled SymSpell frequency file opens with a UTF-8 byte order mark and
    was loaded as plain utf-8, so its first entry was stored as "\ufeffthe".
    Nothing could ever match it, and "teh" was corrected to "tech".
    """
    from echo_link_core.polish_spelling import EnglishDictionary

    dictionary = EnglishDictionary()
    dictionary.warm()
    assert dictionary._symspell is not None
    assert "the" in dictionary._symspell.words
    assert "\ufeffthe" not in dictionary._symspell.words
