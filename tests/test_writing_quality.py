from __future__ import annotations

import json
from pathlib import Path

import pytest

from echo_link_core.writing_quality import (
    evaluate_writing_quality,
    load_writing_quality_corpus,
    reference_responses,
)


ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = ROOT / "benchmarks" / "writing_quality_v1.json"


def _corpus():
    return load_writing_quality_corpus(CORPUS_PATH)


def test_fixed_corpus_covers_supported_writing_quality_categories():
    corpus = _corpus()
    categories = {case.category for case in corpus.cases}
    assert categories == {
        "grammar_punctuation",
        "style_tone",
        "rewrite_meaning",
        "summarization",
        "translation_validation",
    }
    assert len(corpus.cases) == 15
    assert len(corpus.sha256) == 64
    assert len(corpus.limitations) >= 3


def test_locked_references_pass_every_deterministic_check():
    corpus = _corpus()
    first = evaluate_writing_quality(corpus, reference_responses(corpus))
    second = evaluate_writing_quality(corpus, reference_responses(corpus))
    assert first == second
    assert first["offline"] is True
    assert first["deterministic"] is True
    assert first["summary"] == {
        "case_count": 15,
        "passed_count": 15,
        "pass_rate": 1.0,
        "reference_match_count": 15,
        "all_passed": True,
    }
    assert all(value["pass_rate"] == 1.0 for value in first["categories"].values())


def test_grammar_and_punctuation_use_exact_locked_references():
    corpus = _corpus()
    responses = reference_responses(corpus)
    responses["grammar-01"] = "We were ready but the file wasn't saved."
    report = evaluate_writing_quality(corpus, responses)
    result = next(item for item in report["cases"] if item["id"] == "grammar-01")
    assert result["passed"] is False
    assert result["failed_checks"] == ["exact_reference"]


def test_style_markers_reject_wrong_tone_without_claiming_general_quality():
    corpus = _corpus()
    responses = reference_responses(corpus)
    responses["style-01"] = "Hey Sam, send the numbers ASAP."
    report = evaluate_writing_quality(corpus, responses)
    result = next(item for item in report["cases"] if item["id"] == "style-01")
    assert set(result["failed_checks"]) == {"required_terms", "forbidden_terms"}
    assert "fixed-case acceptance" in report["limitations"][0]


def test_rewrite_catches_lost_negation_and_new_number():
    corpus = _corpus()
    responses = reference_responses(corpus)
    responses["rewrite-01"] = "Version 2.4 may upload customer files online after 7 days."
    report = evaluate_writing_quality(corpus, responses)
    result = next(item for item in report["cases"] if item["id"] == "rewrite-01")
    assert set(result["failed_checks"]) == {"required_terms", "no_new_numbers"}


def test_summary_requires_key_facts_and_word_limit():
    corpus = _corpus()
    responses = reference_responses(corpus)
    responses["summary-01"] = (
        "Priya owns packaging for the offline launch on 2026-09-12, but this long answer "
        "drops the open issue and keeps adding extra words without value."
    )
    report = evaluate_writing_quality(corpus, responses)
    result = next(item for item in report["cases"] if item["id"] == "summary-01")
    assert set(result["failed_checks"]) == {"required_terms", "word_limit"}


def test_translation_requires_target_markers_and_protected_values():
    corpus = _corpus()
    responses = reference_responses(corpus)
    responses["translation-01"] = "Please send report Q3 to Ana by 2026-09-01."
    report = evaluate_writing_quality(corpus, responses)
    result = next(item for item in report["cases"] if item["id"] == "translation-01")
    assert set(result["failed_checks"]) == {"changed_source", "target_language_terms"}


def test_missing_response_fails_without_changing_case_count():
    report = evaluate_writing_quality(_corpus(), {})
    assert report["summary"]["case_count"] == 15
    assert report["summary"]["passed_count"] == 0
    assert all("non_empty" in item["failed_checks"] for item in report["cases"])


def test_unknown_response_id_is_rejected():
    with pytest.raises(ValueError, match="Unknown writing-quality response ids"):
        evaluate_writing_quality(_corpus(), {"not-a-case": "text"})


def test_response_values_must_be_text():
    with pytest.raises(ValueError, match="responses must be text"):
        evaluate_writing_quality(_corpus(), {"grammar-01": 7})


def test_single_word_checks_use_word_boundaries():
    corpus = _corpus()
    responses = reference_responses(corpus)
    responses["style-01"] = "Hello Sam, they will please send the numbers soon."
    result = next(
        item
        for item in evaluate_writing_quality(corpus, responses)["cases"]
        if item["id"] == "style-01"
    )
    assert result["checks"]["forbidden_terms"] is True


def test_corpus_rejects_unsupported_commands(tmp_path: Path):
    payload = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    payload["cases"][0]["command"] = "Publish"
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported writing command"):
        load_writing_quality_corpus(path)
