from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from echo_link_core.writing_models import WritingModelBenchmark, WritingModelInfo
from tools import writing_model_probe


MODEL = WritingModelInfo(
    name="qwen2.5:3b",
    digest="sha256:model-digest",
    size_bytes=2_000_000_000,
    modified_at="2026-07-19T12:00:00Z",
    format="gguf",
    family="qwen2",
    parameter_size="3B",
    quantization="Q4_K_M",
)


class _Manager:
    endpoint = "http://127.0.0.1:11434"

    def list_models(self, *, timeout):
        assert timeout == 5.0
        return (MODEL,)

    def benchmark(self, name, *, timeout):
        assert name == MODEL.name
        assert timeout == 30.0
        return WritingModelBenchmark(name, 1.5, 0.5, 1.0, 10, 10.0, True)

    def server_version(self, *, timeout):
        assert timeout == 5.0
        return "0.31.1"


def test_collect_evidence_identifies_configured_model_and_omits_generated_text():
    report = writing_model_probe.collect_evidence(
        _Manager(), "qwen2.5:3b", timeout=30.0
    )
    assert report["status"] == "passed"
    assert report["local_only"] is True
    assert report["configured_model_found"] is True
    assert report["model"]["digest"] == "sha256:model-digest"
    assert report["benchmark"]["response_verified"] is True
    serialized = json.dumps(report)
    assert "prompt" not in serialized.casefold()
    assert "response_text" not in serialized.casefold()


def test_collect_evidence_rejects_missing_configured_model():
    with pytest.raises(RuntimeError, match="not installed locally"):
        writing_model_probe.collect_evidence(_Manager(), "missing:latest", timeout=30.0)


def test_timeout_is_hard_bounded():
    assert writing_model_probe._bounded_timeout("5") == 5.0
    assert writing_model_probe._bounded_timeout("120") == 120.0
    with pytest.raises(argparse.ArgumentTypeError, match="between 5 and 120"):
        writing_model_probe._bounded_timeout("121")


def test_source_provenance_reports_full_commit_and_clean_start(monkeypatch):
    calls = []
    results = iter(
        (
            SimpleNamespace(returncode=0, stdout="A" * 40 + "\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
        )
    )

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return next(results)

    monkeypatch.setattr(writing_model_probe.subprocess, "run", fake_run)
    provenance = writing_model_probe._source_provenance(Path("local-repo"))
    assert provenance == {
        "source_commit": "a" * 40,
        "source_tree_clean_at_probe_start": True,
    }
    assert calls[0][0] == ["git", "rev-parse", "--verify", "HEAD"]
    assert calls[1][0][-1] == "--untracked-files=all"
    assert calls[0][1]["cwd"] == Path("local-repo")
    assert calls[0][1]["check"] is False


def test_source_provenance_marks_dirty_without_exposing_paths(monkeypatch):
    results = iter(
        (
            SimpleNamespace(returncode=0, stdout="1" * 40, stderr=""),
            SimpleNamespace(returncode=0, stdout=" M private-name.txt\n", stderr=""),
        )
    )
    monkeypatch.setattr(
        writing_model_probe.subprocess,
        "run",
        lambda *_args, **_kwargs: next(results),
    )
    provenance = writing_model_probe._source_provenance()
    assert provenance["source_tree_clean_at_probe_start"] is False
    assert "private-name" not in json.dumps(provenance)


def test_main_writes_safe_failure_evidence(monkeypatch, tmp_path: Path):
    output = tmp_path / "writing-model-evidence.json"

    class _Store:
        def __init__(self, _path):
            pass

        def get_settings(self):
            return type("Settings", (), {"ollama_model": "qwen2.5:3b"})()

    class _UnavailableManager:
        def __init__(self, _endpoint):
            raise ValueError("loopback only")

    monkeypatch.setattr(writing_model_probe, "EchoLinkStore", _Store)
    monkeypatch.setattr(writing_model_probe, "LocalWritingModelManager", _UnavailableManager)
    monkeypatch.setattr(
        writing_model_probe,
        "_source_provenance",
        lambda: {
            "source_commit": "f" * 40,
            "source_tree_clean_at_probe_start": True,
        },
    )
    result = writing_model_probe.main(
        ["--endpoint", "https://secret@example.invalid", "--output", str(output)]
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    assert result == 1
    assert report["status"] == "failed"
    assert report["configured_model"] == "qwen2.5:3b"
    assert report["source_commit"] == "f" * 40
    assert report["source_tree_clean_at_probe_start"] is True
    assert "secret@example.invalid" not in json.dumps(report)
