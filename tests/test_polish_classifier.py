from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from echo_link_core.polish_classifier import (
    MiniLMEncoder,
    MiniLMPolishClassifier,
    english_confidence,
    lexical_similarity,
)
from echo_link_core.polish_types import ContextScope, PolishTask


def test_bundled_int8_minilm_loads_and_embeds():
    encoder = MiniLMEncoder()
    assert encoder.warm(), encoder.load_error
    values = encoder.embed(("The report is ready.", "The report is complete."))
    assert values.shape == (2, 384)
    assert float(values[0] @ values[1]) > 0.7


def test_semantic_similarity_accepts_small_correction_and_rejects_reversal():
    classifier = MiniLMPolishClassifier()
    safe = classifier.semantic_similarity(
        "The reports are not ready.",
        "The reports aren't ready.",
    )
    reversal = classifier.semantic_similarity(
        "The reports are not ready.",
        "The ocean is warm and calm.",
    )
    assert safe >= 0.88
    assert reversal < 0.88


def test_classifier_routes_unresolved_agreement_to_llm():
    classifier = MiniLMPolishClassifier()
    decision = classifier.classify(
        "The reports is not ready and it fail.",
        "The reports is not ready and it fail.",
        task=PolishTask.PROOFREAD,
    )
    assert decision.needs_llm
    assert "agreement" in decision.error_categories
    assert decision.context_scope is ContextScope.SENTENCE
    assert decision.english_confidence >= 0.72


def test_classifier_keeps_resolved_simple_edit_rules_only():
    classifier = MiniLMPolishClassifier()
    decision = classifier.classify(
        "Teh report is ready",
        "The report is ready.",
        task=PolishTask.PROOFREAD,
        deterministic_categories=("spelling", "grammar"),
    )
    assert not decision.needs_llm
    assert decision.error_categories == ("spelling", "grammar")


@pytest.mark.parametrize(
    "task",
    (PolishTask.REWRITE, PolishTask.TONE, PolishTask.DOCUMENT),
)
def test_manual_generative_tasks_route_to_llm(task):
    decision = MiniLMPolishClassifier().classify(
        "The report is ready.",
        "The report is ready.",
        task=task,
    )
    assert decision.needs_llm


def test_paragraph_and_document_scope_heads():
    classifier = MiniLMPolishClassifier()
    paragraph = classifier.classify(
        "One sentence. Two sentence. Three sentence. Four sentence.",
        "One sentence. Two sentence. Three sentence. Four sentence.",
        task=PolishTask.PROOFREAD,
    )
    document = classifier.classify(
        "A document.",
        "A document.",
        task=PolishTask.DOCUMENT,
    )
    assert paragraph.context_scope is ContextScope.PARAGRAPH
    assert document.context_scope is ContextScope.DOCUMENT


def test_english_confidence_rejects_non_english_script():
    assert english_confidence("The report is ready.") >= 0.72
    assert english_confidence("これは日本語の文章です。") < 0.72


def test_warm_classifier_meets_local_latency_gate():
    classifier = MiniLMPolishClassifier()
    assert classifier.warm()
    durations = []
    for _ in range(5):
        started = time.perf_counter()
        classifier.classify(
            "The reports is not ready and it fail.",
            "The reports is not ready and it fail.",
            task=PolishTask.PROOFREAD,
        )
        durations.append((time.perf_counter() - started) * 1000)
    assert max(durations) < 150


def test_lexical_similarity_is_safe_fallback():
    assert lexical_similarity("one two three", "one two three") == 1.0
    assert lexical_similarity("one two three", "four five six") == 0.0


def test_locked_classifier_holdout_precision_and_recall():
    path = Path(__file__).parent / "fixtures" / "polish_classifier_holdout.json"
    cases = json.loads(path.read_text(encoding="utf-8"))
    classifier = MiniLMPolishClassifier()
    true_positive = false_positive = false_negative = 0
    for case in cases:
        decision = classifier.classify(
            case["text"],
            case["text"],
            task=PolishTask.PROOFREAD,
        )
        expected = bool(case["needs_llm"])
        true_positive += int(decision.needs_llm and expected)
        false_positive += int(decision.needs_llm and not expected)
        false_negative += int(not decision.needs_llm and expected)
    precision = true_positive / max(1, true_positive + false_positive)
    recall = true_positive / max(1, true_positive + false_negative)
    assert precision == 1.0
    assert recall == 1.0
