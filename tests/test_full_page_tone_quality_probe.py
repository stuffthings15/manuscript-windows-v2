from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from echo_link_app.review_spans import _review_spans
from echo_link_app.rewrite_palette import RewritePalette
from echo_link_core.polish_quality import TONE_CONTRACTS
from echo_link_core.selected_text import COMMANDS
from tools.full_page_tone_quality_probe import (
    SOURCE_TEXT,
    QUICK_SOURCE_TEXT,
    TONES,
    TONE_COMMANDS,
    _deterministic_relation_errors,
    _hud_review_chunks,
    _judge_formula_pass,
    _normalize_judgment,
    _required_term_present,
    _required_meaning_errors,
    _rewrite_full_page,
    _surface_defects,
    _validate_judgment,
)


def test_probe_covers_every_visible_hud_tone():
    # Bound to keep the QApplication alive for the duration of the test; Qt
    # tears down widgets when the last reference to it is dropped.
    app = QApplication.instance() or QApplication([])  # noqa: F841
    palette = RewritePalette()
    visible = tuple(
        palette.tone_combo.itemText(index)
        for index in range(palette.tone_combo.count())
    )

    assert TONES == visible
    assert TONE_COMMANDS["Clear"] == "Rewrite"
    assert all(TONE_COMMANDS[tone] == tone for tone in TONES if tone != "Clear")


def test_engine_supports_every_master_specification_style():
    # The tone menu was cut to seven on 2026-08-13 because the long list was
    # mostly synonyms. The styles below are still reachable as commands and
    # still carry their tone contracts; only the menu shrank.
    app = QApplication.instance() or QApplication([])
    required = {
        "Professional",
        "Academic",
        "Formal",
        "Friendly",
        "Conversational",
        "Concise",
        "Detailed",
        "Persuasive",
        "Confident",
        "Empathetic",
        "Casual",
        "Technical",
        "Simplified",
        "High-school level",
        "College level",
        "Diplomatic",
        "Caveman",
    }
    palette = RewritePalette()
    visible = {
        palette.tone_combo.itemText(index)
        for index in range(palette.tone_combo.count())
    }

    assert visible <= required | {"Clear", "Improve", "Shorten"}
    assert required <= set(TONE_CONTRACTS)
    assert required <= set(COMMANDS)
    palette.close()
    app.processEvents()


def test_probe_uses_one_full_page_generation_while_still_checking_hud_chunks():
    app = QApplication.instance() or QApplication([])
    palette = RewritePalette()
    visible = tuple(
        palette.tone_combo.itemText(index)
        for index in range(palette.tone_combo.count())
    )
    source = "One. Two? Three!\n\nFour. Five.\nSix has no final punctuation"
    expected_spans = _review_spans(source, maximum_sentences=3)
    calls = []

    class _Writer:
        def run(self, text, **kwargs):
            calls.append((text, kwargs))
            return SimpleNamespace(text=text.upper())

    result = _rewrite_full_page(
        _Writer(),
        source=source,
        command="Formal",
        model="test-model",
        timeout=1.0,
    )

    assert visible == TONES
    assert _hud_review_chunks(source) == expected_spans
    assert [span[2] for span in expected_spans] == [3, 2, 1]
    assert [call[0] for call in calls] == [source]
    assert all(call[1]["command"] == "Formal" for call in calls)
    assert result == "ONE. TWO? THREE!\n\nFOUR. FIVE.\nSIX HAS NO FINAL PUNCTUATION"
    assert result.count("\n\n") == source.count("\n\n")
    assert result.count("\n") == source.count("\n")
    palette.close()
    app.processEvents()


def test_full_page_source_contains_every_requested_defect_class():
    defects = _surface_defects(SOURCE_TEXT)

    assert set(defects) == {"spelling", "grammar", "punctuation", "formatting"}
    assert len(defects["spelling"]) >= 10
    assert len(defects["grammar"]) >= 8
    assert len(defects["punctuation"]) >= 6
    assert len(defects["formatting"]) >= 6
    assert len(SOURCE_TEXT.split("\n\n")) == 6
    assert len(SOURCE_TEXT.split()) >= 450


def test_quick_source_keeps_all_error_classes_and_protected_facts():
    defects = _surface_defects(QUICK_SOURCE_TEXT)

    assert set(defects) == {"spelling", "grammar", "punctuation", "formatting"}
    assert "Manuscript" in QUICK_SOURCE_TEXT
    assert "three users" in QUICK_SOURCE_TEXT
    assert "two laptops" in QUICK_SOURCE_TEXT
    assert len(QUICK_SOURCE_TEXT.split()) < 150


def test_surface_checks_accept_clean_standard_english():
    clean = (
        "The project team was ready. The interface was clear.\n\n"
        "Users received the corrected messages. The workflow reads text, separates sentences, and sends groups."
    )

    assert _surface_defects(clean) == {}


def test_default_fact_check_accepts_equivalent_number_format_but_stress_is_exact():
    result = "Old speech records are removed within 15 days."

    assert _required_term_present(result, "fifteen days", exact=False)
    assert not _required_term_present(result, "fifteen days", exact=True)


def test_default_fact_check_handles_compound_number_words_without_substring_false_passes():
    result = "Records are retained for 21 days and the report came from 13 users."

    assert _required_term_present(result, "twenty-one days", exact=False)
    assert not _required_term_present(result, "twenty-one days", exact=True)
    assert not _required_term_present(result, "three users", exact=False)


def _clean_judgment() -> dict:
    return {
        "pass": True,
        "target_style": 4,
        "usefulness": 4,
        "reason": "The rewrite meets the style gate.",
    }


def test_style_judge_pass_formula_is_computed_from_valid_scores():
    source = "The team reviews the report before release."
    result = "The team reviews the report before release."
    clean = _clean_judgment()

    assert _judge_formula_pass(clean)
    _validate_judgment(clean, source=source, result=result)

    inconsistent = {**clean, "target_style": 3}
    assert not _judge_formula_pass(inconsistent)
    _validate_judgment(inconsistent, source=source, result=result)
    assert _normalize_judgment(inconsistent)["pass"] is False

    inverted = {**clean, "pass": False}
    _validate_judgment(inverted, source=source, result=result)
    assert _normalize_judgment(inverted)["pass"] is True

    valid_failure = {**clean, "pass": False, "target_style": 3}
    _validate_judgment(valid_failure, source=source, result=result)

    with pytest.raises(RuntimeError, match="schema mismatch"):
        _validate_judgment(
            {**clean, "grammar": 5},
            source=source,
            result=result,
        )


def test_deterministic_relation_check_catches_before_after_reversal():
    source = "The activation sound played after the speaker started talking."
    reversed_result = "The activation sound played before the speaker started talking."
    equivalent_result = "The speaker started talking before the activation sound played."

    errors = _deterministic_relation_errors(source, reversed_result)

    assert len(errors) == 1
    assert errors[0]["source_quote"] == source
    assert errors[0]["rewrite_quote"] == reversed_result
    assert "before/after" in errors[0]["issue"]
    assert _deterministic_relation_errors(source, equivalent_result) == []


def test_temporal_check_handles_two_preserved_relations_in_one_sentence():
    source = (
        "Manuscript finds the same text before one safe paste; the workflow restores the clipboard "
        "immediately after an approved fallback."
    )
    result = (
        "Manuscript locates the same text before one safe paste; immediately after an approved "
        "fallback, the workflow restores the clipboard."
    )

    assert _deterministic_relation_errors(source, result) == []


def test_deterministic_relation_check_catches_active_actor_action_inversion_only():
    source = "The developers reviewed the error logs."
    inverted = "The error logs reviewed the developers."
    equivalent_passive = "The error logs were reviewed by the developers."

    errors = _deterministic_relation_errors(source, inverted)

    assert len(errors) == 1
    assert errors[0]["source_quote"] == source
    assert errors[0]["rewrite_quote"] == inverted
    assert "Actor and receiver" in errors[0]["issue"]
    assert _deterministic_relation_errors(source, equivalent_passive) == []


def _meaning_safe_stress_result() -> str:
    return """The project team was ready to launch Manuscript after Monday's review. The activation sound played after the speaker started talking. The first shared build opened slowly and failed to insert corrected text into the active document.

The report came from three users who wrote several pages. One tester accepted a spelling fix, but the text did not change and the HUD stayed on the first sentence. The third tester requested a formal tone. The local AI returned a shorter draft, removing two details and changing names that must remain exact.

Reviewers need clear, formal, friendly, and assertive tone versions.

The workflow reads the active selection, separates complete sentences, and sends each group to the model. It restores the normal Windows clipboard after an approved fallback.

Speech records older than fifteen days are deleted. The application should work locally and explain failures. Developers must keep private user writing out of test reports.

Before release, the team must run the page through the HUD and inspect every result."""


def test_fixed_page_meaning_invariants_accept_safe_paraphrases():
    assert _required_meaning_errors(_meaning_safe_stress_result()) == []


def test_fixed_page_meaning_invariants_accept_safe_draft_synonyms():
    result = _meaning_safe_stress_result().replace(
        "a shorter draft, removing two details and changing names",
        "a shortened draft, omitting two details and altering names",
    )

    assert _required_meaning_errors(result) == []


def test_fixed_page_meaning_invariants_accept_nominal_request_and_precise_version():
    result = _meaning_safe_stress_result().replace(
        "The third tester requested a formal tone. The local AI returned a shorter draft, removing "
        "two details and changing names that must remain exact.",
        "The request from the third tester was for a formal tone. The local AI provided a shortened "
        "version, omitting two details and altering names that had to stay precise.",
    )

    assert _required_meaning_errors(result) == []


def test_fixed_page_meaning_invariants_accept_involves_selecting_active_text():
    result = _meaning_safe_stress_result().replace(
        "The workflow reads the active selection",
        "The workflow involves selecting active text",
    )

    assert _required_meaning_errors(result) == []


def test_fixed_page_meaning_invariants_accept_voice_recordings_for_speech_records():
    result = _meaning_safe_stress_result().replace("Speech records", "Voice recordings")

    assert _required_meaning_errors(result) == []


def test_fixed_page_meaning_invariants_accept_split_shared_build_facts():
    result = _meaning_safe_stress_result().replace(
        "The first shared build opened slowly and failed to insert corrected text into the active document.",
        "The first shared build opened slowly. It failed to insert corrected text into the active document.",
    )

    assert _required_meaning_errors(result) == []


def test_fixed_page_meaning_invariants_accept_private_writing_does_not_appear():
    result = _meaning_safe_stress_result().replace(
        "must keep private user writing out of test reports",
        "must ensure private user writing does not appear in test reports",
    )

    assert _required_meaning_errors(result) == []


@pytest.mark.parametrize(
    ("old", "new", "expected_issue"),
    (
        (
            "played after the speaker started talking",
            "played prematurely",
            "Activation sound",
        ),
        (
            "was ready to launch Manuscript after Monday's review",
            "planned to launch Manuscript following Monday's review",
            "ready to launch",
        ),
        (
            "Speech records older than fifteen days are deleted.",
            "The clipboard restoration lasts for fifteen days.",
            "retention period",
        ),
        (
            "keep private user writing out of test reports",
            "keep test reports private",
            "stay out of test reports",
        ),
        (
            "The application should work locally",
            "The application must work locally",
            "should still work locally",
        ),
        (
            "Developers must keep private user writing out of test reports",
            "Developers should keep private user writing out of test reports",
            "stay out of test reports",
        ),
        (
            "the team must run the page through the HUD and inspect every result",
            "before the team runs the page through the HUD, it must inspect every result",
            "through the HUD before",
        ),
        (
            "Reviewers need clear, formal, friendly, and assertive tone versions.",
            "You might need clear, formal, friendly, and assertive tone versions.",
            "Reviewers must remain",
        ),
        (
            "The workflow reads the active selection, separates complete sentences, and sends each group",
            "The workflow reads: Active selection separates complete sentences and sends each group",
            "workflow must read",
        ),
        (
            "opened slowly and failed to insert corrected text",
            "failed to correct text",
            "shared build",
        ),
        (
            "the text did not change and the HUD stayed on the first sentence",
            "the HUD moved on after the spelling fix",
            "accepted spelling fix",
        ),
    ),
)
def test_fixed_page_meaning_invariants_reject_relation_mutations(old, new, expected_issue):
    mutated = _meaning_safe_stress_result().replace(old, new)

    issues = [item["issue"] for item in _required_meaning_errors(mutated)]

    assert any(expected_issue in issue for issue in issues)


def test_fixed_page_meaning_invariants_accept_linked_caveman_sentence_splits():
    result = _meaning_safe_stress_result().replace(
        "The local AI returned a shorter draft, removing two details and changing names that must remain exact.",
        "The local AI made a shorter draft. It removed two details and changed names that must remain exact.",
    ).replace(
        "The workflow reads the active selection, separates complete sentences, and sends each group to the model.",
        "The workflow reads the active selection. It separates complete sentences and sends each group to the model.",
    )

    assert _required_meaning_errors(result) == []
