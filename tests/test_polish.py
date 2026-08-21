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


def test_a_tone_rewrite_gets_the_writers_own_modal_back():
    """A swapped modal is repaired, not argued with.

    Measured across all 20 documents of the 800-percent corpus, 64 of 97
    refused tone rewrites failed on the modal guards alone: "missing may; added
    might", "missing could; added can". The rewrite was otherwise good and the
    writer got the deterministic proofread instead of the tone they asked for.
    Two rounds of prompt work moved it by five points, because in English a
    register shift often IS a modal shift - a model asked to sound friendlier
    reaches for "might" over "may" by doing what it was told.
    """
    from echo_link_core.polish import _restore_tone_modals

    source = "The bird may learn the routine and the owner will notice."
    candidate = "The bird might pick up the routine and the owner will spot it."
    assert _restore_tone_modals(source, candidate) == (
        "The bird may pick up the routine and the owner will spot it."
    )
    # Capitalization follows the position, not the source token.
    assert _restore_tone_modals(
        "May the owner check it.", "Might the owner check it."
    ) == "May the owner check it."


def test_modal_restoration_refuses_anything_it_cannot_be_sure_of():
    """The repair declines every case it cannot do exactly.

    A lost or gained negation is meaning, and meaning is refused rather than
    patched, so a swap involving "cannot" or "won't" is left for the guard. A
    candidate with a different number of modals is not a positional swap at
    all, so it is left alone too - and left alone means the guard refuses it,
    which is the behaviour without this function.
    """
    from echo_link_core.polish import _restore_tone_modals

    negated = ("The bird cannot learn it.", "The bird might not learn it.")
    assert _restore_tone_modals(*negated) == negated[1]

    uneven = ("The bird may learn.", "The bird might learn and may rest.")
    assert _restore_tone_modals(*uneven) == uneven[1]

    already_right = ("The bird may learn.", "The bird may pick it up.")
    assert _restore_tone_modals(*already_right) == already_right[1]


def test_a_proofread_does_not_pass_as_a_friendly_rewrite():
    """Correcting the grammar and stopping there is not warmth.

    The check only fired when the candidate was byte-identical to the source:

        if unchanged and not _FRIENDLY_STYLE_SIGNAL.search(candidate):

    A proofread differs from the source, so it never reached the warmth test at
    all and shipped as a verified Friendly rewrite. Shorten has no equivalent
    escape hatch, and Shorten is the one tone that demonstrably works. Measured
    through Word with a 27B judge, essay 7 scored 1 of 8 paragraphs on Friendly
    and the judge's own words for six of them were that the writing "lacks
    warmth" or is "clear but loses warmth in striving for correctness".

    Fixing this did NOT raise that score - a refused tone candidate still ends
    with the proofread being delivered, so refusing more only changes which
    non-warm text arrives. It is committed because the check was wrong, not
    because it moved a number.
    """
    from echo_link_core.polish import _tone_style_failure

    source = "the dog dont like it and the owner cant tell why"
    proofread = "The dog does not like it, and the owner cannot tell why."
    # Asserted through the contract itself, not the helper: the defect was the
    # condition guarding the call, so a test of the helper alone passes with
    # the bug present and proves nothing.
    assert _tone_style_failure(source, proofread, command="friendly") == (
        "friendly rewrite is not visibly warm"
    )
    warm = "The dog doesn't like it, and the owner can't quite tell why."
    assert _tone_style_failure(source, warm, command="friendly") == ""


def test_warmth_counts_the_signals_this_product_says_it_counts():
    """The detector has to agree with the instruction and the judge.

    The old signal was a narrow word list - glad, happy, help, welcome, please,
    appreciate - while the Friendly task rules and the judge's CRITERIA both
    say contractions, gentle transitions and approachable wording are warmth.
    Enforcing the narrow list alone would have refused genuinely warm writing
    for failing to say "glad".

    Warmth has to be ADDED. A source that already contains contractions is no
    evidence that the rewrite did anything, which is what the byte-identical
    test was clumsily reaching for.

    This is a floor, not a measure of warmth. Against the 27B judge on the same
    documents it reads about three times too high, so it decides whether a
    candidate is refused and it is never a score.
    """
    from echo_link_core.polish import _friendly_rewrite_is_visible

    plain = "the plan is fine"
    assert _friendly_rewrite_is_visible(plain, "We are glad to help with the plan.")
    assert _friendly_rewrite_is_visible(
        plain, "The plan is fine. That way, everyone knows where they stand."
    )
    assert _friendly_rewrite_is_visible(
        "the dog dont like it and the owner cant tell why",
        "The dog doesn't like it, and the owner can't quite tell why.",
    )
    # Already there in the source, so the rewrite added nothing.
    assert not _friendly_rewrite_is_visible(
        "it doesn't matter much", "It doesn't matter much."
    )


def test_rewritten_sentence_opening_is_not_an_invented_name(validator):
    """Capitalizing a rewritten opening is grammar, not a new proper noun.

    Measured on 2026-08-20 in build/refusal-evidence-formal.md and
    build/refusal-evidence-friendly.md: 3 of 11 Formal refusals and 9 of 50
    Friendly refusals were this. "and dismissing that fear" became "Dismissal
    of that fear", "reducing preventable waste" became "Helping to reduce
    preventable waste", and each new first word was read as an invented name
    because `_named_entities` treats any capitalized run as one.

    There was already an exemption for a capital created by SPLITTING a
    run-on. Rewriting the opening is the same kind of capital and had none,
    which meant the guard refused tone rewrites for doing what a tone does.
    """
    result = validator.validate(
        "and dismissing that fear can damage public trust.",
        "Dismissal of that fear can damage public trust.",
        task=PolishTask.TONE,
    )

    assert "new named entity added" not in result.failures, result.failures
