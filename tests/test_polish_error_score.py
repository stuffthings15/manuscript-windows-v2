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
    """
    from echo_link_core.polish_spelling import PolishRuleEngine

    engine = PolishRuleEngine()
    text = "the adress is wrong in esay 1 with a pett"
    repairs = {
        text[value.start : value.end]: value.replacement
        for value in engine.analyze_spelling(text)
    }
    assert repairs.get("esay") == "essay"
    # A collapsed double, not the more frequent "petty".
    assert repairs.get("pett") == "pet"
    # The deleted letter must be half of a doubled pair: "adress" drops a
    # leading "a" to reach "dress", and the unrelated "ss" must not excuse it.
    assert repairs.get("adress") == "address"
