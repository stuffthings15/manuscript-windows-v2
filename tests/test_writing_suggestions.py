from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from echo_link_core.writing_suggestions import (
    SuggestionCategory,
    WritingSuggestionEngine,
    apply_all_suggestions,
    apply_suggestion,
)


def test_categories_and_suggestions_are_stable_and_immutable():
    assert tuple(category.value for category in SuggestionCategory) == (
        "spelling",
        "grammar",
        "formatting",
        "clarity",
    )
    suggestions = WritingSuggestionEngine().analyze("teh item")
    assert suggestions == WritingSuggestionEngine().analyze("teh item")
    with pytest.raises(FrozenInstanceError):
        suggestions[0].replacement = "changed"


def test_analyze_and_apply_all_cover_core_local_rules():
    source = "teh cat cat  is calm , and i smile. this works in order to help"
    engine = WritingSuggestionEngine()
    suggestions = engine.analyze(source)

    assert suggestions
    assert all(0 <= item.start <= item.end <= len(source) for item in suggestions)
    assert all(source[item.start : item.end] == item.original for item in suggestions)
    assert all(
        left.end <= right.start
        for left, right in zip(suggestions, suggestions[1:])
    )
    assert {item.category for item in suggestions} == set(SuggestionCategory)
    assert apply_all_suggestions(source, suggestions) == (
        "The cat is calm, and I smile. This works to help."
    )


def test_protected_urls_emails_paths_and_code_like_tokens_are_untouched():
    source = (
        "open https://example.com/teh  now. Email teh.i@example.com. "
        "Keep `teh  i`, C:\\teh\\file.txt, teh_value, and myTehValue. teh item"
    )
    result = apply_all_suggestions(source, WritingSuggestionEngine().analyze(source))

    assert "https://example.com/teh" in result
    assert "teh.i@example.com" in result
    assert "`teh  i`" in result
    assert "C:\\teh\\file.txt" in result
    assert "teh_value" in result
    assert "myTehValue" in result
    assert result.endswith("The item.")


def test_apply_one_fails_closed_for_stale_text_and_bad_bounds():
    source = "teh value"
    suggestion = WritingSuggestionEngine().analyze(source)[0]
    assert apply_suggestion(source, suggestion) == "The value"

    with pytest.raises(ValueError, match="stale"):
        apply_suggestion("teh other value", suggestion)
    with pytest.raises(ValueError, match="bounds"):
        apply_suggestion(source, replace(suggestion, start=-1))
    with pytest.raises(ValueError, match="matches"):
        forged = replace(suggestion, original="wrong")
        apply_suggestion(source, forged)


def test_apply_all_rejects_overlap_and_accepts_empty_iterable():
    source = "teh item"
    suggestion = WritingSuggestionEngine().analyze(source)[0]
    assert apply_all_suggestions(source, ()) == source
    with pytest.raises(ValueError, match="overlap"):
        apply_all_suggestions(source, (suggestion, suggestion))


def test_empty_text_and_complete_text_need_no_suggestions():
    engine = WritingSuggestionEngine()
    assert engine.analyze("") == ()
    assert engine.analyze("Clear sentence.") == ()
    with pytest.raises(TypeError):
        engine.analyze(None)


def test_device_testing_uses_on_preposition():
    source = "We tested the microphone in two laptops."

    result = apply_all_suggestions(source, WritingSuggestionEngine().analyze(source))

    assert result == "We tested the microphone on two laptops."


def test_schedule_parallelism_and_restrictive_role_name_punctuation():
    source = (
        "The schedule starts at nine, then teams are moving to work zones. "
        "Zone C will be building two benches. "
        "Project lead, Maria Chen, said the gate is open."
    )

    result = apply_all_suggestions(source, WritingSuggestionEngine().analyze(source))

    assert result == (
        "The schedule starts at nine, then teams will move to work zones. "
        "Zone C will build two benches. "
        "Project lead Maria Chen said the gate is open."
    )


def test_full_page_cleanup_repairs_common_errors_and_repeated_punctuation():
    source = (
        "teh team were inconsistant and didnt fix the mesages!! "
        "the recieved draft had grammer and formating errors."
    )

    result = apply_all_suggestions(source, WritingSuggestionEngine().analyze(source))

    assert result == (
        "The team was inconsistent and didn't fix the messages! "
        "The received draft had grammar and formatting errors."
    )


def test_full_page_cleanup_repairs_awkward_generated_phrases():
    source = (
        "The recording stopped without warning a lot. "
        "The third tester request calls for a formal tone. "
        "Two sentences is active. This is a one-safe paste. "
        "The workflow separates sentences, and sends each group. "
        "These changes make review confusing because completion is unclear?"
    )

    result = apply_all_suggestions(source, WritingSuggestionEngine().analyze(source))

    assert result == (
        "The recording stopped frequently without warning. "
        "The third tester requested a formal tone. "
        "Two sentences are active. This is one safe paste. "
        "The workflow separates sentences and sends each group. "
        "These changes make review confusing because completion is unclear."
    )


def test_qwen3_cleanup_repairs_remaining_subject_verb_agreement():
    source = (
        "The interface were crowded. The correction card were large. "
        "The card need to shrink. Reviewers needs clear marks. "
        "No private sentence are uploaded."
    )

    result = apply_all_suggestions(source, WritingSuggestionEngine().analyze(source))

    assert result == (
        "The interface was crowded. The correction card was large. "
        "The card needs to shrink. Reviewers need clear marks. "
        "No private sentence is uploaded."
    )


def test_three_parallel_predicates_keep_the_serial_comma():
    source = "The workflow reads active selection, separates sentences, and sends each group."

    result = apply_all_suggestions(source, WritingSuggestionEngine().analyze(source))

    assert result == source


def test_a_hand_punctuated_list_missing_only_its_last_comma_gets_one():
    """The rule this replaces was memorized, not general.

    "staffing, security, utilities and transit" was corrected because that exact
    sentence was written into the proofread table from a benchmark page, while
    the identical defect in any other sentence was left alone.
    """
    assert _applied("The estimate includes staffing, security, utilities and transit.") == (
        "The estimate includes staffing, security, utilities, and transit."
    )
    assert _applied("We reviewed the logs, the alerts, the queue and the archive.") == (
        "We reviewed the logs, the alerts, the queue, and the archive."
    )


def test_a_single_comma_is_not_enough_to_call_something_a_list():
    """The false positive that set the threshold at two commas.

    "Visitors will not pay, although the city continues to provide staffing and
    security" has one comma, before a subordinate clause. Reading that as a list
    produced "provide staffing, and security". One comma cannot distinguish a
    list from a clause boundary here, so "apples, pears and plums" is knowingly
    left alone: a wrong comma is worse than a missing one.
    """
    for text in (
        "Visitors will not pay, although the city continues to provide staffing and security.",
        "She bought apples, pears and plums.",
        "The red and blue house is ours.",
        "Keep customer names gate codes and service notes private.",
        "He left and she stayed.",
    ):
        assert _applied(text) == text


def test_an_appositive_is_left_for_a_human():
    """"Ann, my manager, my mentor and my friend" may be one person or four."""
    source = "I spoke to Ann, my manager, my mentor and my friend."

    assert _applied(source) == source


def test_a_trailing_clause_is_not_treated_as_a_list_item():
    source = "We reviewed the logs, the alerts, the queue and the archive, which we kept."

    assert _applied(source) == source


def _applied(text: str) -> str:
    from echo_link_core.writing_suggestions import (
        WritingSuggestionEngine,
        apply_all_suggestions,
    )

    engine = WritingSuggestionEngine()
    return apply_all_suggestions(text, engine.analyze(text))


def test_doubled_commas_are_collapsed():
    """The last remaining artefact in eight of nine otherwise clean essays."""
    assert ",," not in _applied("In Utrecht, the Netherlands,, this is true.")
    assert ",," not in _applied("Feeding the fish, replacing water,, cleaning it.")


def test_a_comma_left_after_a_full_stop_is_removed():
    assert "." in _applied("or other equipment., but the animal is fine.")
    assert ".," not in _applied("or other equipment., but the animal is fine.")
    assert ". ," not in _applied("We saw it. , then left.")


def test_abbreviations_keep_the_comma_that_punctuates_their_sentence():
    """The guard that matters: "p.m.," is punctuating, not doubled. A lookback
    that only reads the final letters sees "m" and strips a correct comma."""
    for text in (
        "At 12:40 p.m., observers arrived.",
        "The team met at 9 a.m., then left.",
        "Etc., and so on.",
    ):
        assert text == _applied(text), text


def test_a_word_repeated_with_a_stray_comma_is_collapsed():
    """The corrector produced "replacing water,, water,, cleaning" - a repeated
    word carrying a doubled comma, which the whitespace-only rule never saw."""
    assert _applied("replacing water,, water,, cleaning it.") == (
        "Replacing water, cleaning it."
    )
    assert _applied("land beyond the city,, city,, and more.") == (
        "Land beyond the city, and more."
    )


def test_a_single_comma_between_repeats_is_left_alone():
    """"New York, New York" is correct English. A rule that flattened it would
    be worse than the defect it fixes."""
    for text in (
        "We moved to New York, New York last year.",
        "The town of Puebla, Puebla is large.",
    ):
        assert _applied(text) == text, text


def test_plain_repeated_words_still_collapse():
    assert _applied("the the pet needs care.") == "the pet needs care."
