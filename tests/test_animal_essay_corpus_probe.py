from __future__ import annotations

from copy import deepcopy

import pytest

from tools import animal_essay_corpus_probe as probe


def _essay() -> dict[str, object]:
    paragraphs = [f"Original paragraph {index} has enough words to remain complete." for index in range(1, 11)]
    return {
        "page_id": "sample",
        "text": "\n".join(paragraphs),
        "paragraph_count": 10,
    }


def _passing_report() -> dict[str, object]:
    essay = _essay()
    final = [f"Corrected paragraph {index} has enough words to remain complete." for index in range(1, 11)]
    return {
        "page_id": essay["page_id"],
        "original_text": essay["text"],
        "paragraph_count": essay["paragraph_count"],
        "final_text": "\n".join(final),
        "sections": [
            {
                "counter": f"{index} of 10",
                "accepted": True,
                "skipped": False,
                "ai_review_ready": True,
            }
            for index in range(1, 11)
        ],
        "completed": True,
        "changed": True,
    }


def test_default_corpus_is_local_locked_and_complete() -> None:
    assert probe.CORPUS == probe.ROOT / "test-corpus" / "animal-pet-20"
    assert probe.validate_corpus(probe.CORPUS) == []

    essays = probe.load_essays(probe.CORPUS)

    assert len(essays) == 20
    assert {essay["paragraph_count"] for essay in essays} == {10}


def test_release_summary_requires_all_twenty_essays() -> None:
    reports = [_passing_report() for _ in range(19)]
    for index, report in enumerate(reports, start=1):
        report["page_id"] = f"essay-{index}"

    summary = probe.build_summary(reports, generated_at="2026-08-04T00:00:00+00:00")

    assert summary["required_essays"] == 20
    assert summary["reported_essays"] == 19
    assert summary["passed"] is False


def test_release_summary_rejects_duplicate_essay_reports() -> None:
    reports = [_passing_report() for _ in range(20)]

    summary = probe.build_summary(reports, generated_at="2026-08-04T00:00:00+00:00")

    assert summary["unique_essays"] == 1
    assert summary["passed"] is False


def test_report_requires_exact_ordered_section_progression() -> None:
    report = _passing_report()
    report["sections"][3]["counter"] = "5 of 10"

    failures = probe.validate_report(report)

    assert "section counters were not exactly 1 of 10 through 10 of 10" in failures


@pytest.mark.parametrize(
    "broken_section",
    [
        {"accepted": False, "skipped": True, "ai_review_ready": True},
        {"accepted": True, "skipped": False, "ai_review_ready": False},
    ],
    ids=["skip", "rules-only"],
)
def test_report_rejects_skip_and_non_ai_sections(
    broken_section: dict[str, bool],
) -> None:
    report = _passing_report()
    report["sections"][4].update(broken_section)

    failures = probe.validate_report(report)

    assert any("every section" in failure for failure in failures)


def test_report_rejects_truncated_final_text() -> None:
    report = deepcopy(_passing_report())
    report["final_text"] = "\n".join(str(report["final_text"]).splitlines()[:5])

    failures = probe.validate_report(report)

    assert "final text was truncated or lost paragraph boundaries" in failures
