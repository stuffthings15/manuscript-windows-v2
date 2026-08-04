from __future__ import annotations

import pytest

from echo_link_core.readability import analyze_readability


def test_clear_text_gets_local_readability_report():
    report = analyze_readability("The app runs locally. It keeps your text private.")
    assert report.word_count == 9
    assert report.sentence_count == 2
    assert report.score >= 60
    assert report.label in {"Easy", "Clear"}
    assert "Readability score:" in report.as_text()
    assert "No common readability problems found." in report.as_text()


def test_report_flags_long_filler_and_passive_phrasing():
    source = (
        "This very complicated implementation was created by the team and it is actually designed "
        "to basically demonstrate how a sentence can continue for far too many words without giving "
        "the reader a useful pause or a clear place to stop."
    )
    report = analyze_readability(source)
    assert report.long_sentence_count == 1
    assert report.filler_count >= 3
    assert report.passive_voice_count >= 1
    assert any("Split" in item for item in report.suggestions)
    assert any("filler" in item for item in report.suggestions)


def test_empty_readability_input_is_rejected():
    with pytest.raises(ValueError, match="Select text"):
        analyze_readability("  ")
