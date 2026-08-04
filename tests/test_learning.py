from __future__ import annotations

import pytest

from echo_link_core.learning import (
    LearningProposal,
    apply_learned_corrections,
    propose_learning,
    validate_learning_rule,
)


def test_proposal_contains_only_changed_spans():
    assert propose_learning("Send teh report to curtis.", "Send the report to Curtis.") == (
        LearningProposal("teh", "the"),
        LearningProposal("curtis", "Curtis"),
    )


@pytest.mark.parametrize(
    ("suggested", "accepted"),
    [
        ("Send the report.", "Please send the report."),
        ("Open the report.", "Open C:\\Work\\report.txt."),
        ("Use API_KEY now.", "Use SECRET_KEY now."),
        ("Meet at two.", "Meet at 2:00."),
        ("One two three four.", "Alpha beta gamma delta."),
    ],
)
def test_proposal_rejects_insert_sensitive_and_ambiguous_edits(suggested, accepted):
    assert propose_learning(suggested, accepted) == ()


@pytest.mark.parametrize(
    ("source", "replacement"),
    [
        ("", "value"),
        ("same", "same"),
        ("a", "the"),
        ("line\nbreak", "line break"),
        ("API_KEY", "API key"),
        ("two", "2"),
        ("file", "report.txt"),
    ],
)
def test_validation_rejects_unsafe_rules(source, replacement):
    with pytest.raises(ValueError):
        validate_learning_rule(source, replacement)


def test_matching_is_boundary_safe_casefolded_longest_and_single_pass():
    rules = {
        "echo": "voice",
        "echo link": "Echo-Link",
        "teh": "echo",
    }
    assert apply_learned_corrections("ECHO LINK fixes teh echoing", rules) == (
        "Echo-Link fixes echo echoing"
    )


def test_matching_caps_replacements_and_never_cascades():
    text = " ".join(["teh"] * 12)
    result = apply_learned_corrections(text, {"teh": "the", "the": "THE"})
    assert result.split().count("the") == 10
    assert result.split().count("teh") == 2
    assert "THE" not in result


def test_learning_supports_local_unicode_word_pairs():
    assert validate_learning_rule("resume", "résumé") == LearningProposal("resume", "résumé")
    assert apply_learned_corrections("Send RESUME today", {"resume": "résumé"}) == (
        "Send résumé today"
    )
