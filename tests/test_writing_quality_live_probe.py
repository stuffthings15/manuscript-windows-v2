from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from echo_link_core.selected_text import WritingResult
from echo_link_core.writing_quality import load_writing_quality_corpus
from tools import writing_quality_live_probe as probe


PRIVATE_MODEL = "PRIVATE_MODEL_NEVER_PERSIST:latest"
PRIVATE_RESPONSE = "PRIVATE_RESPONSE_NEVER_PERSIST"


class _ReferenceAssistant:
    endpoint = "http://127.0.0.1:11434"

    def __init__(self, corpus):
        self.cases = {case.source: case for case in corpus.cases}
        self.calls = []

    def run(self, text, **kwargs):
        case = self.cases[text]
        self.calls.append((case.identifier, kwargs))
        return WritingResult(
            case.reference,
            kwargs["command"],
            kwargs["model"],
            kwargs["profile"],
        )


def _corpus():
    return load_writing_quality_corpus(probe.DEFAULT_CORPUS)


def _assert_content_free(report: dict) -> None:
    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)
    corpus = _corpus()
    for case in corpus.cases:
        assert case.source not in serialized
        assert case.reference not in serialized
    assert PRIVATE_MODEL not in serialized
    assert PRIVATE_RESPONSE not in serialized
    assert '"source"' not in serialized.casefold()
    assert '"prompt"' not in serialized.casefold()
    assert '"response"' not in serialized.casefold()


def test_collect_evidence_runs_locked_cases_and_keeps_only_safe_metrics():
    corpus = _corpus()
    assistant = _ReferenceAssistant(corpus)
    report = probe.collect_evidence(assistant, PRIVATE_MODEL, corpus, timeout=30.0)

    assert report["status"] == "passed"
    assert report["counts"] == {
        "case_count": 15,
        "completed_case_count": 15,
        "execution_failed_count": 0,
        "failed_count": 0,
        "passed_count": 15,
        "quality_failed_count": 0,
        "reference_match_count": 15,
    }
    assert report["checks"]["loopback_127_0_0_1_verified"] is True
    assert report["checks"]["no_cloud_fallback"] is True
    assert len(report["cases"]) == 15
    assert [item["id"] for item in report["cases"]] == [case.identifier for case in corpus.cases]
    assert all(len(item["hashes"]["response_sha256"]) == 64 for item in report["cases"])
    assert all(
        set(item) == {"id", "status", "checks", "hashes", "counts"}
        for item in report["cases"]
    )
    assert len(assistant.calls) == 15
    assert all(call[1]["model"] == PRIVATE_MODEL for call in assistant.calls)
    assert all(call[1]["timeout"] == 30.0 for call in assistant.calls)
    _assert_content_free(report)


def test_collect_evidence_rejects_any_endpoint_except_127_0_0_1():
    corpus = _corpus()
    for endpoint in (
        "http://localhost:11434",
        "http://[::1]:11434",
        "https://127.0.0.1:11434",
        "http://example.invalid:11434",
        "http://127.0.0.1:11434/api",
    ):
        assistant = _ReferenceAssistant(corpus)
        assistant.endpoint = endpoint
        with pytest.raises(probe.ProbeVerificationError, match="endpoint_not_127_0_0_1"):
            probe.collect_evidence(assistant, PRIVATE_MODEL, corpus)
        assert assistant.calls == []


def test_case_failure_continues_and_records_accurate_content_free_counts():
    corpus = _corpus()
    raw_error = "PRIVATE_RESPONSE_NEVER_PERSIST model failure"

    class _FailingAssistant(_ReferenceAssistant):
        def run(self, text, **kwargs):
            case = self.cases[text]
            self.calls.append((case.identifier, kwargs))
            if case.identifier == "summary-02":
                raise RuntimeError(raw_error)
            return WritingResult(
                case.reference,
                kwargs["command"],
                kwargs["model"],
                kwargs["profile"],
            )

    assistant = _FailingAssistant(corpus)
    report = probe.collect_evidence(
        assistant,
        PRIVATE_MODEL,
        corpus,
        timeout=30.0,
    )
    failed = next(item for item in report["cases"] if item["id"] == "summary-02")
    assert report["status"] == "failed"
    assert report["counts"] == {
        "case_count": 15,
        "completed_case_count": 14,
        "execution_failed_count": 1,
        "failed_count": 1,
        "passed_count": 14,
        "quality_failed_count": 0,
        "reference_match_count": 14,
    }
    assert len(assistant.calls) == 15
    assert failed == {
        "id": "summary-02",
        "status": "execution_failed",
        "checks": {
            "execution_completed": False,
            "quality_checks_completed": False,
        },
        "hashes": {
            "failure_type_sha256": hashlib.sha256(b"RuntimeError").hexdigest()
        },
        "counts": {"attempt_count": 1, "failed_check_count": 0},
    }
    assert raw_error not in json.dumps(report)
    _assert_content_free(report)


def test_quality_failure_is_separate_from_execution_failure():
    corpus = _corpus()

    class _WrongGrammarAssistant(_ReferenceAssistant):
        def run(self, text, **kwargs):
            case = self.cases[text]
            self.calls.append((case.identifier, kwargs))
            output = "Still wrong." if case.identifier == "grammar-01" else case.reference
            return WritingResult(
                output,
                kwargs["command"],
                kwargs["model"],
                kwargs["profile"],
            )

    report = probe.collect_evidence(
        _WrongGrammarAssistant(corpus),
        PRIVATE_MODEL,
        corpus,
    )
    failed = report["cases"][0]
    assert failed["status"] == "failed_checks"
    assert failed["checks"]["execution_completed"] is True
    assert failed["checks"]["exact_reference"] is False
    assert report["counts"]["completed_case_count"] == 15
    assert report["counts"]["execution_failed_count"] == 0
    assert report["counts"]["quality_failed_count"] == 1
    assert report["counts"]["passed_count"] == 14
    _assert_content_free(report)


def test_main_refuses_dirty_tree_before_store_or_model(monkeypatch, tmp_path: Path):
    output = tmp_path / "dirty.json"
    opened = []

    class _Store:
        def __init__(self, *_args):
            opened.append(True)

    monkeypatch.setattr(probe, "EchoLinkStore", _Store)
    monkeypatch.setattr(
        probe,
        "_source_provenance",
        lambda: {"source_commit": "a" * 40, "source_tree_clean_at_probe_start": False},
    )
    assert probe.main(["--output", str(output)]) == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert opened == []
    assert report["provenance"]["source_commit"] == "a" * 40
    assert report["provenance"]["source_tree_clean_at_probe_start"] is False
    _assert_content_free(report)


def test_main_uses_configured_model_and_default_locked_corpus(monkeypatch, tmp_path: Path):
    output = tmp_path / "passed.json"
    corpus = _corpus()
    assistants = []

    class _Store:
        def __init__(self, _path):
            pass

        def get_settings(self):
            return SimpleNamespace(ollama_model=PRIVATE_MODEL)

    class _Assistant(_ReferenceAssistant):
        def __init__(self, endpoint):
            super().__init__(corpus)
            self.endpoint = endpoint
            assistants.append(self)

    monkeypatch.setattr(probe, "EchoLinkStore", _Store)
    monkeypatch.setattr(probe, "LocalWritingAssistant", _Assistant)
    monkeypatch.setattr(
        probe,
        "_source_provenance",
        lambda: {"source_commit": "b" * 40, "source_tree_clean_at_probe_start": True},
    )
    assert probe.main(["--output", str(output), "--timeout", "30"]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert len(assistants) == 1
    assert len(assistants[0].calls) == 15
    assert report["provenance"] == {
        "source_commit": "b" * 40,
        "source_tree_clean_at_probe_start": True,
    }
    _assert_content_free(report)


def test_report_write_is_atomic(tmp_path: Path, monkeypatch):
    output = tmp_path / "evidence.json"
    output.write_text("old", encoding="utf-8")
    calls = []
    real_replace = os.replace

    def replace(source, destination):
        calls.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(probe.os, "replace", replace)
    probe._write_report({"schema_version": 1, "status": "passed"}, output)
    assert len(calls) == 1
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "passed"
    assert not calls[0][0].exists()


def test_source_provenance_keeps_commit_and_clean_flag_only(monkeypatch):
    results = iter(
        (
            SimpleNamespace(returncode=0, stdout="C" * 40, stderr=""),
            SimpleNamespace(returncode=0, stdout=" M PRIVATE_FILE.txt\n", stderr=""),
        )
    )
    monkeypatch.setattr(probe.subprocess, "run", lambda *_args, **_kwargs: next(results))
    assert probe._source_provenance(Path("repo")) == {
        "source_commit": "c" * 40,
        "source_tree_clean_at_probe_start": False,
    }


def test_default_output_is_under_evidence_and_timeout_is_bounded():
    assert probe.DEFAULT_OUTPUT.parent == probe.ROOT / "evidence"
    assert probe._bounded_timeout("5") == 5.0
    assert probe._bounded_timeout("120") == 120.0
    with pytest.raises(argparse.ArgumentTypeError, match="between 5 and 120"):
        probe._bounded_timeout("121")
