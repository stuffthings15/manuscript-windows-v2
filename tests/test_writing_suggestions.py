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


def test_a_verb_left_behind_by_an_unfinished_edit_is_removed():
    """This replaced eleven rules that each memorized one sentence.

    Six spelled out the backup sentence and five the evidence sentence. None
    fired on any other text, so the same defect in a student essay was ignored.
    """
    for stray in ("needs", "needed", "retained", "accessed", "referenced", "requested"):
        assert _applied(f"Marcus must not overwrite {stray} the earlier backup.") == (
            "Marcus must not overwrite the earlier backup."
        )
    assert _applied("The claim does not follow stemmed from the report.") == (
        "The claim does not follow from the report."
    )
    assert _applied("She should not delete kept the file.") == (
        "She should not delete the file."
    )


def test_the_same_defect_after_do_support_is_repaired_too():
    """Five more memorized rules, one per stray verb after "did not measure"."""
    for stray in ("affects", "analysed", "assessed"):
        assert _applied(f"The study did not measure {stray} the property values.") == (
            "The study did not measure the property values."
        )


def test_a_participle_used_as_an_adjective_is_never_deleted():
    """Why the rule still demands a determiner or preposition after the verb.

    Dropping that requirement covered the "did not measure" sentences but broke
    ordinary English, because a past participle doubles as an adjective:
    "We can use measured data" became "We can use data".
    """
    for text in (
        "We can use measured data.",
        "The notice must show affected users.",
        "Staff should keep reviewed copies.",
    ):
        assert _applied(text) == text


def test_a_modal_with_have_or_be_keeps_its_participle():
    """A perfect tense reads as modal, verb, participle, and is correct English.

    These four are the cases that actually exercise the guard: each has a
    determiner right after the participle, so without excluding have and be the
    rule deletes the participle and "should have kept the file" becomes "should
    have the file". The four sentences below were verified to match the pattern
    with the exclusion removed.
    """
    for text in (
        "You should have kept the file.",
        "We must have used the backup.",
        "They could have followed the plan.",
        "She should have reviewed the report.",
        "You should have looked at the report.",
        "The file must be kept for seven years.",
    ):
        assert _applied(text) == text


def test_a_plural_noun_after_a_modal_is_never_mistaken_for_a_stray_verb():
    """The reason the verb list is closed rather than keyed on the -s ending.

    "Residents must pay taxes the council sets" has the identical shape, and
    deleting "taxes" would destroy the sentence.
    """
    for text in (
        "Residents must pay taxes the council sets.",
        "We must keep tracking the changes.",
        "They must not overwrite the earlier backup.",
        "He will need the earlier backup.",
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


def _repaired(source: str) -> str:
    return apply_all_suggestions(source, WritingSuggestionEngine().analyze(source))


# A stray line break between the two copies. The 500-percent corpus seeds this
# 75 times across its 20 essays and every one survived, because only a space, a
# tab or a run of commas was matched as a separator.
REPEATED_ACROSS_A_BREAK = (
    ("neither is sufficient by itself\nitself.", "itself itself"),
    ("a serious discussion\ndiscussion must examine the people.", "discussion discussion"),
    ("the report is credible\ncredible enough to use.", "credible credible"),
)

# A repeat the rule must refuse. Each is correct writing, and the first two were
# being damaged before this: a reduplicated place name is not a stutter.
NOT_A_STUTTER = (
    ("We visited Bora Bora last spring.", "Bora"),
    ("They met in Walla Walla, Washington.", "Walla"),
    ("The band played in Baden Baden.", "Baden"),
    # A capital at the start of a line is what a new paragraph or a heading
    # looks like, and a captured Word document arrives with its paragraphs in
    # one string.
    ("Water\nWater management is expensive.", "Water"),
    ("Chapter One\nOne of the birds escaped.", "One"),
    # A blank line is a paragraph boundary, never a stray break.
    ("the tank was full\n\nfull marks were given.", "full"),
)


@pytest.mark.parametrize(("source", "pair"), REPEATED_ACROSS_A_BREAK)
def test_a_word_repeated_across_a_line_break_is_repaired(source: str, pair: str) -> None:
    repaired = _repaired(source)
    first, second = pair.split()
    assert repaired.count(first) == 1, repaired
    assert "\n" + second not in repaired


@pytest.mark.parametrize(("source", "kept"), NOT_A_STUTTER)
def test_a_repeat_that_is_not_a_stutter_is_left_alone(source: str, kept: str) -> None:
    assert _repaired(source).count(kept) == 2, source


def test_a_stutter_at_the_start_of_a_sentence_is_still_repaired() -> None:
    """Only the second copy has to be lowercase, so these keep working."""
    assert _repaired("By by the time the team arrived, the floor was wet.") == (
        "By the time the team arrived, the floor was wet."
    )
    assert _repaired("This is the the gate.") == "This is the gate."


def test_correct_doublings_are_not_collapsed():
    """"had had" is past perfect, not a stutter.

    The corrector turned "She had had enough" into "She had enough" - a
    different tense and a different meaning, in text the writer got right.

    Found on 2026-08-28 by reading what the rule does rather than from a report,
    which is the point: nobody complains about a word quietly going missing, and
    `polish_error_score` scores the same doubling as an error with the same
    naive backreference. The grader agrees with the defect, so optimising the
    corpus score can never surface this - the metric rewards the damage.
    """
    from echo_link_core.writing_suggestions import (
        WritingSuggestionEngine,
        apply_all_suggestions,
    )

    engine = WritingSuggestionEngine()

    for sentence in (
        "She had had enough of it.",
        "It was very very cold that morning.",
        "There were many many reasons to wait.",
    ):
        assert apply_all_suggestions(sentence, engine.analyze(sentence)) == sentence


def test_a_real_stutter_is_still_repaired():
    """The guard must not turn into an excuse to stop proofreading."""
    from echo_link_core.writing_suggestions import (
        WritingSuggestionEngine,
        apply_all_suggestions,
    )

    engine = WritingSuggestionEngine()

    repaired = apply_all_suggestions(
        "We make secure procedures procedures simpler.",
        engine.analyze("We make secure procedures procedures simpler."),
    )

    assert repaired == "We make secure procedures simpler."


def test_a_full_stop_after_a_question_mark_is_removed():
    """"matter?." reached the finished document, and is always wrong.

    It survives because the sentence already ends correctly, so nothing
    downstream treats the leftover stop as a defect. polish_error_score charges
    it and no rule removed it, so essay 10 of the gated corpus carried one all
    the way through on 2026-08-28.
    """
    from echo_link_core.writing_suggestions import (
        WritingSuggestionEngine,
        apply_all_suggestions,
    )

    engine = WritingSuggestionEngine()

    for damaged, repaired in (
        ("Why does it matter?. The next point follows.",
         "Why does it matter? The next point follows."),
        ("It works!. Then it stops.", "It works! Then it stops."),
    ):
        assert apply_all_suggestions(damaged, engine.analyze(damaged)) == repaired


def test_deliberate_terminal_punctuation_is_left_alone():
    """"?!" is a choice and "..." is an ellipsis. Neither is a mistake.

    The rule is deliberately narrow for this reason: only a period directly
    after ? or !, with nothing but spaces between.
    """
    from echo_link_core.writing_suggestions import (
        WritingSuggestionEngine,
        apply_all_suggestions,
    )

    engine = WritingSuggestionEngine()
    sentence = "Really?! I had no idea."

    assert apply_all_suggestions(sentence, engine.analyze(sentence)) == sentence
