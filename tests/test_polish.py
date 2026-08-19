from __future__ import annotations


import pytest

from echo_link_core.polish import PolishValidator, minimal_edits
from echo_link_core.polish_classifier import MiniLMPolishClassifier
from echo_link_core.polish_spelling import PolishRuleEngine, normalize_polish_text
from echo_link_core.writing_suggestions import SuggestionCategory
from echo_link_core.polish_types import PolishTask


def test_normalization_preserves_formatting_boundaries():
    source = "Cafe\u0301  item\r\n\tNext line"
    normalized = normalize_polish_text(source)
    assert normalized.startswith("Caf\u00e9")
    assert "\r\n\t" in normalized


def test_rule_engine_uses_existing_rules_and_symspell():
    engine = PolishRuleEngine()
    assert engine.warm(), engine.dictionary.load_error
    result = engine.correct("Teh adress is definately wrong  ")
    assert result.text == "The address is definitely wrong. "
    # Assert what a reader gets, not which internal bucket reported it. The two
    # tuples used to split on which map held the word; now one shared map does,
    # so whichever layer reaches a span first claims it. Both callers in
    # polish.py concatenate the tuples - for the category list and for the
    # suggestion count - so the split was never observable behaviour.
    suggestions = (*result.rule_suggestions, *result.spelling_suggestions)
    assert suggestions
    corrected = {value.original.casefold() for value in suggestions}
    assert {"teh", "adress", "definately"} <= corrected, corrected
    assert any(
        value.category is SuggestionCategory.SPELLING for value in suggestions
    )


def test_rule_engine_respects_user_and_application_words():
    result = PolishRuleEngine().correct(
        "Curtis uses Manuscript and Roxybox daily",
        user_terms=("Curtis", "Roxybox"),
    )
    assert "Curtis" in result.text
    assert "Manuscript" in result.text
    assert "Roxybox" in result.text


def test_rule_engine_never_guesses_at_capitalized_sentence_start_name():
    result = PolishRuleEngine().correct("Malik will correct the notice.")

    assert result.text == "Malik will correct the notice."


def test_warm_deterministic_correction_meets_latency_gate():
    engine = PolishRuleEngine()
    assert engine.warm()
    durations = [
        engine.correct("Teh report is ready  and it has a adress").elapsed_ms
        for _ in range(8)
    ]
    assert max(durations) < 50


@pytest.fixture(scope="module")
def validator():
    classifier = MiniLMPolishClassifier()
    assert classifier.warm()
    return PolishValidator(classifier)


def test_validator_accepts_safe_grammar_correction(validator):
    source = "The reports is not ready, and it fail."
    candidate = "The reports are not ready, and they fail."
    # Actor changes are deliberately rejected. The safe version keeps "it".
    unsafe = validator.validate(source, candidate, task=PolishTask.PROOFREAD)
    safe = validator.validate(
        source,
        "The reports are not ready, and it fails.",
        task=PolishTask.PROOFREAD,
    )
    assert not unsafe.accepted
    assert safe.accepted
    assert safe.semantic_similarity >= 0.88


def test_validator_allows_redundant_they_to_be_replaced_by_retained_plural_noun(
    validator,
):
    source = (
        "Several department plans were submitted late, and they contain "
        "conflicting instructions."
    )
    candidate = (
        "Several department plans were delayed and contain conflicting instructions."
    )

    result = validator.validate(source, candidate, task=PolishTask.TONE)

    assert result.accepted, result.failures


def test_validator_allows_equivalent_hard_obligation_and_ordering_words(validator):
    result = validator.validate(
        "Every participant must check in before orientation.",
        "Every participant is required to check in prior to orientation.",
        task=PolishTask.TONE,
    )

    assert result.accepted, result.failures


def test_validator_allows_during_to_introduce_an_existing_named_time(validator):
    result = validator.validate(
        "Last spring, two visitors entered the hallway.",
        "During last spring's exercise, two visitors entered the hallway.",
        task=PolishTask.TONE,
    )

    assert result.accepted, result.failures


def test_validator_treats_while_keeping_as_a_manner_modifier(validator):
    result = validator.validate(
        "The measures reduce known access and supervision risks while making "
        "clear that emergency procedures remain unchanged.",
        "The measures reduce known access and supervision risks while keeping "
        "emergency procedures unchanged.",
        task=PolishTask.TONE,
    )

    assert result.accepted, result.failures


def test_validator_allows_possessive_their_for_retained_each_owner(validator):
    result = validator.validate(
        "Every participant must store personal phones.",
        "Each participant must store their personal phones.",
        task=PolishTask.TONE,
    )

    assert result.accepted, result.failures


def test_validator_allows_equivalent_blank_reading_and_following_wording(validator):
    result = validator.validate(
        "Both units must stay in range, but the system shows a blank reading "
        "after users reconnect the sensor.",
        "These units are required to stay in range, yet the system shows no "
        "reading following users' reconnection of the sensor.",
        task=PolishTask.TONE,
    )

    assert result.accepted, result.failures


def test_validator_allows_corrected_relative_time_possessive(validator):
    result = validator.validate(
        "Last weeks rehearsal ended after nineteen minutes.",
        "Last week's rehearsal ended after nineteen minutes.",
        task=PolishTask.PROOFREAD,
    )

    assert result.accepted, result.failures


def test_validator_rejects_invented_no_reading_state(validator):
    result = validator.validate(
        "The system shows a valid reading.",
        "The system shows no reading.",
        task=PolishTask.TONE,
    )

    assert not result.accepted
    assert any(f.startswith("negation changed") for f in result.failures)


def test_validator_rejects_added_possessive_without_a_source_owner(validator):
    result = validator.validate(
        "Store personal phones.",
        "Store their personal phones.",
        task=PolishTask.TONE,
    )

    assert not result.accepted
    assert any(x.startswith("actor changed") for x in result.failures)


def test_validator_rejects_removed_they_without_a_source_antecedent(validator):
    result = validator.validate(
        "They contain conflicting instructions.",
        "The plans contain conflicting instructions.",
        task=PolishTask.TONE,
    )

    assert not result.accepted
    assert any(x.startswith("actor changed") for x in result.failures)


@pytest.mark.parametrize(
    ("source", "candidate", "expected_failure"),
    (
        (
            "Build 47 must remain before July 23, 2026.",
            "Build 48 must remain before July 23, 2026.",
            "protected facts",
        ),
        (
            "Keep https://example.com unchanged.",
            "Keep https://example.org unchanged.",
            "protected facts",
        ),
        (
            "The report is not ready.",
            "The report is ready.",
            "negation",
        ),
        (
            "The team must ship it.",
            "The team should ship it.",
            "obligation",
        ),
        (
            "Run the test before deployment.",
            "Run the test after deployment.",
            "ordering",
        ),
        (
            "Curtis approved the report.",
            "Curtis and Morgan approved the report.",
            "named entity",
        ),
        (
            "The system failed for the user.",
            "We fixed it for the customer.",
            "actor",
        ),
        (
            "Use `safe_mode` in app.config.",
            "Use `unsafe_mode` in app.config.",
            "code",
        ),
    ),
)
def test_validator_rejects_adversarial_fact_changes(
    validator,
    source,
    candidate,
    expected_failure,
):
    result = validator.validate(source, candidate, task=PolishTask.PROOFREAD)
    assert not result.accepted
    assert any(expected_failure in value for value in result.failures)


def test_validator_allows_normal_sentence_start_capitalization(validator):
    source = "the project was late. during the review, the team fixed it."
    candidate = "The project was late. During the review, the team fixed it."
    result = validator.validate(source, candidate, task=PolishTask.PROOFREAD)
    assert result.accepted


def test_validator_allows_weekday_capitalization(validator):
    source = "The team met on monday and reviewed the build."
    candidate = "The team met on Monday and reviewed the build."
    result = validator.validate(source, candidate, task=PolishTask.PROOFREAD)
    assert result.accepted


def test_validator_allows_sentence_start_during_plus_weekday_capitalization(validator):
    source = "during monday's meeting Daniel explain the workflow."
    candidate = "During Monday's meeting, Daniel explained the workflow."
    result = validator.validate(source, candidate, task=PolishTask.PROOFREAD)
    assert result.accepted


def test_validator_allows_sentence_case_before_multiword_name(validator):
    result = validator.validate(
        "the Redwood Valley Community Clinic team is ready.",
        "The Redwood Valley Community Clinic team is ready.",
        task=PolishTask.PROOFREAD,
    )

    assert result.accepted


def test_validator_allows_grammar_inflection_without_erasing_failure(validator):
    result = validator.validate(
        "Build 47 fail.",
        "Build 47 fails.",
        task=PolishTask.PROOFREAD,
    )
    assert result.accepted, result.failures


def test_validator_allows_contraction_double_negative_and_obligation_inflections(validator):
    result = validator.validate(
        "Dogs dont judge nobody because it need patience.",
        "Dogs don't judge anyone because it needs patience.",
        task=PolishTask.PROOFREAD,
    )

    assert result.accepted, result.failures


def test_validator_allows_apostropheless_shouldnt_and_double_negative_repair(validator):
    result = validator.validate(
        "Dogs dont mean nothing bad, and they shouldnt of chewed shoes.",
        "Dogs do not mean anything bad, and they should not have chewed shoes.",
        task=PolishTask.PROOFREAD,
    )

    assert result.accepted, result.failures

def test_validator_allows_apostropheless_shouldnt_repair(validator):
    result = validator.validate(
        "They shouldnt chew shoes.",
        "They should not chew shoes.",
        task=PolishTask.PROOFREAD,
    )

    assert result.accepted, result.failures

def test_validator_allows_have_not_contraction_expansion(validator):
    result = validator.validate(
        "Technicians haven't determined the cause, and no one may upload it.",
        "Technicians have not determined the cause. No one may upload it.",
        task=PolishTask.PROOFREAD,
    )

    assert result.accepted, result.failures


def test_validator_allows_neither_or_to_neither_nor_repair(validator):
    result = validator.validate(
        "Neither erased values or estimated temperatures is allowed.",
        "Neither erased values nor estimated temperatures are allowed.",
        task=PolishTask.PROOFREAD,
    )

    assert result.accepted, result.failures


def test_validator_rejects_changed_single_name_at_sentence_start(validator):
    result = validator.validate(
        "Malik will correct the notice.",
        "Mali will correct the notice.",
        task=PolishTask.PROOFREAD,
    )

    assert not result.accepted
    assert "new named entity added" in result.failures
    assert "named entity removed" in result.failures


def test_validator_allows_contrast_while_to_repair_comma_splice(validator):
    result = validator.validate(
        "Visitors may use the lab, the history room remains closed.",
        "Visitors may use the lab, while the history room remains closed.",
        task=PolishTask.PROOFREAD,
    )

    assert result.accepted, result.failures


def test_validator_rejects_large_grammar_rewrite(validator):
    result = validator.validate(
        "The report is ready.",
        "The detailed technical report is now completely ready for every customer.",
        task=PolishTask.PROOFREAD,
    )
    assert not result.accepted
    assert any("35%" in value for value in result.failures)


def test_validator_rejects_paragraph_structure_change(validator):
    result = validator.validate(
        "First paragraph.\n\nSecond paragraph.",
        "First paragraph. Second paragraph.",
        task=PolishTask.PROOFREAD,
    )
    assert not result.accepted
    assert "paragraph structure changed" in result.failures


def test_validator_applies_grammar_limits_to_document_proofread(validator):
    result = validator.validate(
        "One short paragraph.",
        "One short paragraph.\n\nA new paragraph with several invented words.",
        task=PolishTask.DOCUMENT,
        grammar_mode=True,
    )
    assert not result.accepted
    assert "paragraph structure changed" in result.failures


def test_minimal_edits_produce_replace_insert_and_delete_ranges():
    edits = minimal_edits("They is ready now.", "They are ready.")
    assert edits
    rebuilt = "They is ready now."
    for edit in reversed(edits):
        rebuilt = (
            rebuilt[: edit.original_start]
            + edit.replacement
            + rebuilt[edit.original_end :]
        )
    assert rebuilt == "They are ready."


def test_minimal_edits_empty_when_text_is_unchanged():
    assert minimal_edits("Same.", "Same.") == ()


class _FixedSimilarity:
    """A classifier that reports one similarity, so a floor can be pinned.

    The real embedding scores a short safe paraphrase above 0.95, which clears
    every floor and would make the test below pass whether the fix were present
    or not. The refusals this fix is about came from windows cut out of long
    error-dense paragraphs, which a two-line fixture cannot reproduce. Holding
    the number still tests the thing that actually changed: which floor a tone
    command is judged against.
    """

    def __init__(self, value: float) -> None:
        self.value = float(value)

    def semantic_similarity(self, source: str, candidate: str) -> float:
        return self.value

    def warm(self) -> bool:
        return True


def test_a_tone_rewrite_is_not_held_to_the_proofread_wording_floor():
    """A rewrite asked to change the wording must not fail for changing it.

    Until 2026-08-18 the floor was 0.74 for a proofread of dense text, 0.78 for
    any other proofread, and 0.88 for everything else - and "everything else"
    is every tone command, because grammar_mode is true only for Proofread. So
    the one operation explicitly asked to reword was held to the strictest
    standard in the system while proofreading got the loosest. Measured on the
    800-percent corpus, 4 of 36 tone rewrites survived, and the refused ones
    scored 0.777 to 0.880 - inside the band a correct dense proofread already
    occupies. The writer clicked Shorten and got the deterministic repair back:
    645 characters in, 646 out.

    0.80 is accepted and 0.70 is still refused, so the band is pinned from both
    sides rather than simply opened.
    """
    source = "The gardener can keep a pigeon, and the bird will learn the routine."
    candidate = "The gardener can house a pigeon, and the bird will pick up the routine."

    lenient = PolishValidator(_FixedSimilarity(0.80))
    strict = PolishValidator(_FixedSimilarity(0.70))

    assert lenient.validate(source, candidate, task=PolishTask.REWRITE).accepted
    assert not strict.validate(source, candidate, task=PolishTask.REWRITE).accepted


def test_a_tone_rewrite_still_fails_every_meaning_guard(validator):
    """Only the wording floor moved. Meaning is protected exactly as before.

    This is the half that matters: the floor was lowered for tone, so the guard
    that catches a rewrite changing what the text CLAIMS has to be shown still
    firing on the same task.
    """
    source = "The gardener can keep a pigeon, and the bird will learn the routine."
    flipped = validator.validate(
        source,
        "The gardener cannot keep a pigeon, and the bird will learn the routine.",
        task=PolishTask.REWRITE,
    )
    swapped_modal = validator.validate(
        source,
        "The gardener can keep a pigeon, and the bird might learn the routine.",
        task=PolishTask.REWRITE,
    )
    assert not flipped.accepted
    assert not swapped_modal.accepted


def test_tone_commands_tell_the_model_to_keep_every_modal():
    """The prompt listed facts, negation and polarity, and stopped there.

    The model then wrote "will" -> "might" and dropped "can", and the modal
    guards refused it - correctly, because that changes what the text claims.
    The fix constrains the model rather than the guard, so what is asserted
    here is that a tone command carries the rule and a proofread does not need
    it (proofreading is not asked to reword, and its own rules already forbid
    dropping a level of force).
    """
    from echo_link_core.selected_text import _task_rules

    friendly = _task_rules("Friendly", "", None, hard_word_limit=False)
    shorten = _task_rules("Shorten", "", None, hard_word_limit=False)
    assert "modal verb" in friendly
    assert "Do not swap one modal for another" in friendly
    assert "modal verb" in shorten
