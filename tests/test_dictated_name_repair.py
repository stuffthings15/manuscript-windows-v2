"""A dictated name should be spelled the way the open document spells it.

Curtis reported names being mangled when dictating into a document that
already cites them. Measured 2026-09-21, the corrector was not the culprit:
`deterministic_cleanup` leaves a correctly spelled name untouched, and the
entity protector already protects one from capitalisation alone with no
document at all. The transcriber is the weak link - the name arrives one or
two letters wrong and every stage downstream defends the wrong word.

These cover the repair that closes that gap, and the three gates that stop it
becoming the 2026-08-07 incident in reverse, where a correct word is rewritten
into a typo.
"""

from __future__ import annotations

from echo_link_core.correction import CorrectionPipeline
from echo_link_core.models import DictationMode, DictationSettings
from echo_link_core.polish_entities import repair_dictated_names


DOCUMENT = (
    "Petrova and Kahneman disagree about replication. "
    "Petrova argues the evidence is thin; Kahneman does not. "
    "Tversky is cited throughout, and Petrova returns to Tversky later."
)


def test_a_mis_heard_name_is_spelled_the_way_the_document_spells_it():
    assert repair_dictated_names("I agree with Pethrova", DOCUMENT)[0] == (
        "I agree with Petrova"
    )
    assert repair_dictated_names("Kahnemann said otherwise", DOCUMENT)[0] == (
        "Kahneman said otherwise"
    )
    assert repair_dictated_names("Tverski disagreed", DOCUMENT)[0] == (
        "Tversky disagreed"
    )


def test_an_ordinary_english_word_is_never_repaired_into_a_name():
    """The 2026-08-07 incident in reverse, and the reason for the word gate.

    "form" sits one substitution from the surname "Ford". Without the
    ordinary-word gate the repair runs backwards and rewrites correct English
    into a name the writer never said: measured, "The form was signed" becomes
    "The Ford was signed". That is the same class of failure as freezing "Wen"
    as a name and losing a 627-character section to it.
    """
    document = "Ford published the study. Ford again, and Ford once more."

    assert repair_dictated_names("The form was signed", document)[0] == (
        "The form was signed"
    )

    # "Wen" is short enough that the inventory floor already excludes it, so
    # this one is covered twice over rather than by the word gate alone.
    short = "Wen is the author here. Wen again, and Wen once more."
    assert repair_dictated_names("When did that happen", short)[0] == (
        "When did that happen"
    )


def test_a_truncated_name_is_a_different_person_and_is_refused():
    document = "Malik led the study. Malik again, and Malik once more."

    assert repair_dictated_names("Mali led it", document)[0] == "Mali led it"


def test_two_equally_close_names_are_a_coin_flip_and_are_refused():
    document = (
        "Larsen and Larsan both wrote. Larsen again. Larsan again. "
        "Larsen once more. Larsan once more."
    )

    assert repair_dictated_names("Larsun disagreed", document)[0] == "Larsun disagreed"


def test_a_possessive_keeps_its_suffix():
    repaired, repairs = repair_dictated_names("Pethrova's argument holds", DOCUMENT)

    assert repaired == "Petrova's argument holds"
    assert repairs == 1


def test_a_correct_name_is_left_alone_and_counted_as_no_repair():
    repaired, repairs = repair_dictated_names("I agree with Petrova", DOCUMENT)

    assert repaired == "I agree with Petrova"
    assert repairs == 0


def test_no_document_means_no_repair():
    assert repair_dictated_names("Pethrova agreed", "") == ("Pethrova agreed", 0)


def test_fast_mode_gets_the_repair_because_it_never_calls_the_model():
    """Fast mode inserts deterministic text straight into the target.

    It is where a mis-heard name is least defended, so the repair lives in the
    deterministic pass rather than beside the model.
    """
    pipeline = CorrectionPipeline(
        lambda: {},
        DictationSettings(mode=DictationMode.FAST),
    )

    result = pipeline.deterministic(
        "I agree with Pethrova",
        document_context=DOCUMENT,
    )

    assert "Petrova" in result.text
    assert "Pethrova" not in result.text


def test_the_setting_turns_it_off_completely():
    settings = DictationSettings(mode=DictationMode.FAST)
    settings.document_context_enabled = False
    pipeline = CorrectionPipeline(lambda: {}, settings)

    result = pipeline.deterministic(
        "I agree with Pethrova",
        document_context=DOCUMENT,
    )

    assert "Pethrova" in result.text
