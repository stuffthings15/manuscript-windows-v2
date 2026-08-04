from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .selected_text import COMMANDS, PROFILES


_CATEGORIES = frozenset(
    {
        "grammar_punctuation",
        "style_tone",
        "rewrite_meaning",
        "summarization",
        "translation_validation",
    }
)
_CHECK_KEYS = frozenset(
    {
        "exact_reference",
        "forbidden_terms",
        "max_words",
        "preserve_numbers",
        "protected_terms",
        "reject_new_numbers",
        "require_change",
        "required_terms",
        "target_language_terms",
    }
)
_NUMBER = re.compile(r"(?<!\w)(?:[$€£])?\d+(?:[.:/-]\d+)*(?!\w)")
_WORD = re.compile(r"\b[\w'-]+\b", re.UNICODE)


@dataclass(frozen=True, slots=True)
class WritingQualityCase:
    identifier: str
    category: str
    command: str
    profile: str
    source: str
    reference: str
    target_language: str
    checks: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class WritingQualityCorpus:
    name: str
    cases: tuple[WritingQualityCase, ...]
    limitations: tuple[str, ...]
    sha256: str


def load_writing_quality_corpus(path: str | Path) -> WritingQualityCorpus:
    source = Path(path)
    raw = source.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Writing-quality corpus must be valid UTF-8 JSON.") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("Writing-quality corpus must use schema version 1.")
    name = str(payload.get("name") or "").strip()
    raw_cases = payload.get("cases")
    raw_limitations = payload.get("limitations")
    if not name or not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("Writing-quality corpus needs a name and cases.")
    if not isinstance(raw_limitations, list) or not all(
        isinstance(item, str) and item.strip() for item in raw_limitations
    ):
        raise ValueError("Writing-quality corpus needs explicit limitations.")

    cases = tuple(_parse_case(item) for item in raw_cases)
    identifiers = [case.identifier for case in cases]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Writing-quality case ids must be unique.")
    return WritingQualityCorpus(
        name=name,
        cases=cases,
        limitations=tuple(item.strip() for item in raw_limitations),
        sha256=hashlib.sha256(raw).hexdigest().upper(),
    )


def evaluate_writing_quality(
    corpus: WritingQualityCorpus,
    responses: Mapping[str, str],
) -> dict[str, Any]:
    """Score fixed synthetic responses without running a model or network call."""
    unknown_ids = sorted(set(responses) - {case.identifier for case in corpus.cases})
    if unknown_ids:
        raise ValueError(f"Unknown writing-quality response ids: {', '.join(unknown_ids)}")
    invalid_ids = sorted(identifier for identifier, value in responses.items() if not isinstance(value, str))
    if invalid_ids:
        raise ValueError(f"Writing-quality responses must be text: {', '.join(invalid_ids)}")

    results = [_evaluate_case(case, responses.get(case.identifier) or "") for case in corpus.cases]
    categories: dict[str, dict[str, int | float]] = {}
    for category in sorted(_CATEGORIES):
        category_results = [result for result in results if result["category"] == category]
        if not category_results:
            continue
        passed = sum(bool(result["passed"]) for result in category_results)
        categories[category] = {
            "case_count": len(category_results),
            "passed_count": passed,
            "pass_rate": round(passed / len(category_results), 4),
        }
    passed_count = sum(bool(result["passed"]) for result in results)
    exact_count = sum(bool(result["reference_match"]) for result in results)
    return {
        "schema_version": 1,
        "corpus_name": corpus.name,
        "corpus_sha256": corpus.sha256,
        "offline": True,
        "deterministic": True,
        "limitations": list(corpus.limitations),
        "summary": {
            "case_count": len(results),
            "passed_count": passed_count,
            "pass_rate": round(passed_count / len(results), 4),
            "reference_match_count": exact_count,
            "all_passed": passed_count == len(results),
        },
        "categories": categories,
        "cases": results,
    }


def reference_responses(corpus: WritingQualityCorpus) -> dict[str, str]:
    """Return locked references for corpus integrity checks, not model evidence."""
    return {case.identifier: case.reference for case in corpus.cases}


def _parse_case(raw: Any) -> WritingQualityCase:
    if not isinstance(raw, dict):
        raise ValueError("Each writing-quality case must be an object.")
    values = {
        field: str(raw.get(field) or "").strip()
        for field in ("id", "category", "command", "profile", "source", "reference")
    }
    if any(not value for value in values.values()):
        raise ValueError("Each writing-quality case needs id, category, command, profile, source, and reference.")
    if values["category"] not in _CATEGORIES:
        raise ValueError(f"Unknown writing-quality category: {values['category']}")
    if values["command"] not in COMMANDS:
        raise ValueError(f"Unsupported writing command: {values['command']}")
    if values["profile"] not in PROFILES:
        raise ValueError(f"Unsupported writing profile: {values['profile']}")
    target_language = str(raw.get("target_language") or "").strip()
    if values["command"] == "Translate" and not target_language:
        raise ValueError(f"Translation case {values['id']} needs a target language.")
    checks = raw.get("checks")
    if not isinstance(checks, dict) or not checks:
        raise ValueError(f"Writing-quality case {values['id']} needs checks.")
    unknown_checks = sorted(set(checks) - _CHECK_KEYS)
    if unknown_checks:
        raise ValueError(f"Unknown checks for {values['id']}: {', '.join(unknown_checks)}")
    _validate_checks(values["id"], checks)
    return WritingQualityCase(
        identifier=values["id"],
        category=values["category"],
        command=values["command"],
        profile=values["profile"],
        source=values["source"],
        reference=values["reference"],
        target_language=target_language,
        checks=dict(checks),
    )


def _validate_checks(identifier: str, checks: Mapping[str, Any]) -> None:
    list_checks = ("forbidden_terms", "protected_terms", "required_terms", "target_language_terms")
    for key in list_checks:
        value = checks.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
            raise ValueError(f"Check {key} for {identifier} must be a list of text terms.")
    for key in ("exact_reference", "preserve_numbers", "reject_new_numbers", "require_change"):
        if key in checks and not isinstance(checks[key], bool):
            raise ValueError(f"Check {key} for {identifier} must be true or false.")
    if "max_words" in checks:
        max_words = checks["max_words"]
        if isinstance(max_words, bool) or not isinstance(max_words, int) or max_words < 1:
            raise ValueError(f"Check max_words for {identifier} must be a positive integer.")


def _evaluate_case(case: WritingQualityCase, response: str) -> dict[str, Any]:
    candidate = response.strip()
    checks: dict[str, bool] = {"non_empty": bool(candidate)}
    requested = case.checks
    if requested.get("exact_reference"):
        checks["exact_reference"] = candidate == case.reference
    if requested.get("require_change"):
        checks["changed_source"] = _normalized(candidate) != _normalized(case.source)
    required = tuple(requested.get("required_terms") or ())
    if required:
        checks["required_terms"] = all(_contains(candidate, term) for term in required)
    forbidden = tuple(requested.get("forbidden_terms") or ())
    if forbidden:
        checks["forbidden_terms"] = not any(_contains(candidate, term) for term in forbidden)
    protected = tuple(requested.get("protected_terms") or ())
    if protected:
        checks["protected_terms"] = all(term in candidate for term in protected)
    language_terms = tuple(requested.get("target_language_terms") or ())
    if language_terms:
        checks["target_language_terms"] = all(_contains(candidate, term) for term in language_terms)
    if requested.get("preserve_numbers"):
        checks["preserved_numbers"] = not (Counter(_numbers(case.source)) - Counter(_numbers(candidate)))
    if requested.get("reject_new_numbers"):
        checks["no_new_numbers"] = not (Counter(_numbers(candidate)) - Counter(_numbers(case.source)))
    max_words = requested.get("max_words")
    if max_words is not None:
        checks["word_limit"] = len(_WORD.findall(candidate)) <= max_words

    failed = [name for name, passed in checks.items() if not passed]
    return {
        "id": case.identifier,
        "category": case.category,
        "command": case.command,
        "profile": case.profile,
        "target_language": case.target_language or None,
        "passed": not failed,
        "failed_checks": failed,
        "checks": checks,
        "reference_match": candidate == case.reference,
        "response": candidate,
    }


def _contains(text: str, term: str) -> bool:
    target = term.casefold()
    candidate = text.casefold()
    if re.fullmatch(r"[\w'-]+", target, re.UNICODE):
        return re.search(rf"(?<!\w){re.escape(target)}(?!\w)", candidate, re.UNICODE) is not None
    return target in candidate


def _normalized(text: str) -> str:
    return " ".join(text.casefold().split())


def _numbers(text: str) -> tuple[str, ...]:
    return tuple(match.group(0) for match in _NUMBER.finditer(text))
