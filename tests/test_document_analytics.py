from __future__ import annotations

import pytest

from echo_link_core.document_analytics import analyze_document


def test_document_analytics_reports_counts_score_and_reading_time():
    report = analyze_document(
        "Echo-Link works locally. It keeps the draft private.\n\n"
        "The review stays under the user's control."
    )

    assert report.word_count == 15
    assert report.sentence_count == 3
    assert report.paragraph_count == 2
    assert report.reading_time_label == "Under 1 min"
    assert 0 <= report.readability_score <= 100
    assert report.readability_label in {"Easy", "Clear", "Difficult", "Very difficult"}
    assert report.long_sentence_count == 0
    assert report.guidance == ("No common document-level problems found.",)


def test_document_analytics_flags_long_filler_and_weak_wording():
    report = analyze_document(
        "I think there are actually very many reasons in order to keep extending this "
        "single sentence with additional wording even though the main idea was already "
        "clear to every reader who reached the middle of the statement and kept reading."
    )

    assert report.long_sentence_count == 1
    assert report.filler_count == 2
    assert report.weak_wording_count == 3
    assert {flag.term for flag in report.filler_flags} == {"actually", "very"}
    assert {flag.term for flag in report.weak_wording_flags} == {
        "I think",
        "in order to",
        "there are",
    }
    assert report.long_sentence_previews
    assert any("Break up" in item for item in report.guidance)
    assert any("filler" in item for item in report.guidance)
    assert any("weak phrase" in item for item in report.guidance)


def test_document_analytics_rejects_empty_text():
    with pytest.raises(ValueError, match="Write or load"):
        analyze_document(" \n ")


def test_document_analytics_is_deterministic():
    source = "Maybe this is somewhat unclear. The next sentence is direct."
    assert analyze_document(source) == analyze_document(source)
