from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from echo_link_core.local_chat import ChatCitation, LocalChatResult
from tools import local_chat_probe


PRIVATE_MODEL = "raw-model-name-never-persist:latest"
PRIVATE_FACT = "RAW_FACT_NEVER_PERSIST_7F3A"
PRIVATE_SOURCE = (
    "# RAW_SOURCE_NEVER_PERSIST\n\n"
    f"The approved source verification code is {PRIVATE_FACT}.\n"
)
PRIVATE_QUESTION = (
    "What is the approved source verification code? RAW_QUESTION_NEVER_PERSIST"
)
PRIVATE_ANSWER = (
    f"The approved source verification code is {PRIVATE_FACT}. "
    "RAW_ANSWER_NEVER_PERSIST [S1]."
)
MATERIAL = local_chat_probe.ProbeMaterial(
    source_text=PRIVATE_SOURCE,
    question=PRIVATE_QUESTION,
    expected_fact=PRIVATE_FACT,
)


class _MockChat:
    endpoint = "http://127.0.0.1:11434"

    def __init__(self, *, answer: str = PRIVATE_ANSWER, source_id: str = "S1"):
        self.answer = answer
        self.source_id = source_id
        self.approved_path: Path | None = None
        self.received_model = ""

    def ask(self, question, *, sources, model, timeout):
        assert question == PRIVATE_QUESTION
        assert timeout == 30.0
        assert len(sources.approved_paths) == 1
        self.approved_path = Path(sources.approved_paths[0])
        assert self.approved_path.suffix == ".md"
        assert self.approved_path.read_text(encoding="utf-8") == PRIVATE_SOURCE
        self.received_model = model
        match = sources.search("approved source verification code", limit=1)[0]
        citation = ChatCitation(
            self.source_id,
            match.path,
            match.line_number,
            match.snippet,
        )
        return LocalChatResult(self.answer, model, (citation,))


def _assert_no_raw_content(report: dict, *, approved_path: Path | None = None) -> None:
    serialized = json.dumps(report, sort_keys=True)
    forbidden = (
        PRIVATE_MODEL,
        PRIVATE_FACT,
        "RAW_SOURCE_NEVER_PERSIST",
        "RAW_QUESTION_NEVER_PERSIST",
        "RAW_ANSWER_NEVER_PERSIST",
    )
    for value in forbidden:
        assert value not in serialized
    if approved_path is not None:
        assert str(approved_path) not in serialized
    assert '"prompt"' not in serialized.casefold()


def test_collect_evidence_uses_one_temporary_source_and_omits_all_raw_text(
    tmp_path: Path,
):
    chat = _MockChat()
    temp_parent = tmp_path / "temporary-sources"
    temp_parent.mkdir()

    report = local_chat_probe.collect_evidence(
        chat,
        PRIVATE_MODEL,
        timeout=30.0,
        material=MATERIAL,
        temp_parent=temp_parent,
    )

    assert report["status"] == "passed"
    assert report["hashes"]["source_sha256"] == hashlib.sha256(
        PRIVATE_SOURCE.encode("utf-8")
    ).hexdigest()
    assert report["hashes"]["answer_sha256"] == hashlib.sha256(
        PRIVATE_ANSWER.encode("utf-8")
    ).hexdigest()
    assert report["counts"]["approved_source_count"] == 1
    assert report["counts"]["structured_s1_citation_count"] == 1
    assert report["counts"]["answer_s1_marker_count"] == 1
    assert report["provenance"]["configured_model_used"] is True
    assert report["provenance"]["loopback_endpoint_verified"] is True
    assert report["provenance"]["temporary_source_removed"] is True
    assert chat.received_model == PRIVATE_MODEL
    assert chat.approved_path is not None
    assert not chat.approved_path.exists()
    assert list(temp_parent.iterdir()) == []
    _assert_no_raw_content(report, approved_path=chat.approved_path)


@pytest.mark.parametrize(
    ("chat", "error_code"),
    [
        (_MockChat(source_id="S2"), "structured_s1_citation_missing"),
        (
            _MockChat(answer=f"The code is {PRIVATE_FACT}. RAW_ANSWER_NEVER_PERSIST"),
            "answer_s1_marker_missing",
        ),
        (
            _MockChat(answer="A different value. RAW_ANSWER_NEVER_PERSIST [S1]."),
            "expected_fact_missing",
        ),
    ],
)
def test_collect_evidence_requires_structured_s1_and_answer_verification(
    chat,
    error_code: str,
    tmp_path: Path,
):
    with pytest.raises(local_chat_probe.ProbeVerificationError, match=error_code):
        local_chat_probe.collect_evidence(
            chat,
            PRIVATE_MODEL,
            timeout=30.0,
            material=MATERIAL,
            temp_parent=tmp_path,
        )
    assert chat.approved_path is not None
    assert not chat.approved_path.exists()


def test_collect_evidence_rejects_non_loopback_before_creating_source(tmp_path: Path):
    chat = _MockChat()
    chat.endpoint = "https://example.invalid:11434"
    with pytest.raises(
        local_chat_probe.ProbeVerificationError,
        match="non_loopback_endpoint",
    ):
        local_chat_probe.collect_evidence(
            chat,
            PRIVATE_MODEL,
            timeout=30.0,
            material=MATERIAL,
            temp_parent=tmp_path,
        )
    assert list(tmp_path.iterdir()) == []


def test_report_write_atomically_replaces_destination(tmp_path: Path, monkeypatch):
    output = tmp_path / "local-chat-evidence.json"
    output.write_text("old evidence", encoding="utf-8")
    report = {"schema_version": 1, "status": "passed", "hashes": {"value": "a" * 64}}
    calls = []
    real_replace = os.replace

    def replace(source, destination):
        calls.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(local_chat_probe.os, "replace", replace)
    local_chat_probe._write_report(report, output)

    assert json.loads(output.read_text(encoding="utf-8")) == report
    assert len(calls) == 1
    temporary, destination = calls[0]
    assert temporary.parent == output.parent
    assert destination == output.resolve()
    assert not temporary.exists()


def test_main_reads_current_configured_model_and_writes_safe_evidence(
    tmp_path: Path,
    monkeypatch,
):
    output = tmp_path / "local-chat-evidence.json"
    database = tmp_path / "echo-link.db"
    store_paths = []
    chats = []

    class _Store:
        def __init__(self, path):
            store_paths.append(path)

        def get_settings(self):
            return SimpleNamespace(ollama_model=PRIVATE_MODEL)

    class _ConfiguredChat(_MockChat):
        def __init__(self, endpoint):
            super().__init__()
            self.endpoint = endpoint
            chats.append(self)

    monkeypatch.setattr(local_chat_probe, "EchoLinkStore", _Store)
    monkeypatch.setattr(local_chat_probe, "ApprovedSourceChat", _ConfiguredChat)
    monkeypatch.setattr(local_chat_probe, "_new_probe_material", lambda: MATERIAL)
    monkeypatch.setattr(
        local_chat_probe,
        "_source_provenance",
        lambda: {
            "source_commit": "f" * 40,
            "source_tree_clean_at_probe_start": True,
        },
    )

    result = local_chat_probe.main(
        [
            "--database",
            str(database),
            "--output",
            str(output),
            "--timeout",
            "30",
        ]
    )
    report = json.loads(output.read_text(encoding="utf-8"))

    assert result == 0
    assert store_paths == [database]
    assert len(chats) == 1
    assert chats[0].received_model == PRIVATE_MODEL
    assert report["provenance"]["source_commit"] == "f" * 40
    assert report["provenance"]["source_tree_clean_at_probe_start"] is True
    _assert_no_raw_content(report, approved_path=chats[0].approved_path)


def test_main_failure_evidence_hashes_type_and_omits_error_message(
    tmp_path: Path,
    monkeypatch,
):
    output = tmp_path / "failed-local-chat-evidence.json"
    raw_error = "RAW_MODEL_ERROR_NEVER_PERSIST with private answer text"

    class _Store:
        def __init__(self, _path):
            pass

        def get_settings(self):
            return SimpleNamespace(ollama_model=PRIVATE_MODEL)

    class _FailingChat(_MockChat):
        def __init__(self, endpoint):
            super().__init__()
            self.endpoint = endpoint

        def ask(self, *_args, **_kwargs):
            raise RuntimeError(raw_error)

    monkeypatch.setattr(local_chat_probe, "EchoLinkStore", _Store)
    monkeypatch.setattr(local_chat_probe, "ApprovedSourceChat", _FailingChat)
    monkeypatch.setattr(local_chat_probe, "_new_probe_material", lambda: MATERIAL)
    monkeypatch.setattr(
        local_chat_probe,
        "_source_provenance",
        lambda: {
            "source_commit": "e" * 40,
            "source_tree_clean_at_probe_start": False,
        },
    )

    result = local_chat_probe.main(["--output", str(output), "--timeout", "30"])
    report = json.loads(output.read_text(encoding="utf-8"))
    serialized = json.dumps(report)

    assert result == 1
    assert report["status"] == "failed"
    assert report["provenance"]["failure_stage"] == "live_chat_probe"
    assert report["hashes"]["failure_type_sha256"] == hashlib.sha256(
        b"RuntimeError"
    ).hexdigest()
    assert raw_error not in serialized
    _assert_no_raw_content(report)


def test_timeout_is_hard_bounded():
    assert local_chat_probe._bounded_timeout("5") == 5.0
    assert local_chat_probe._bounded_timeout("120") == 120.0
    with pytest.raises(argparse.ArgumentTypeError, match="between 5 and 120"):
        local_chat_probe._bounded_timeout("121")


def test_source_provenance_keeps_only_commit_and_clean_flag(monkeypatch):
    results = iter(
        (
            SimpleNamespace(returncode=0, stdout="A" * 40 + "\n", stderr=""),
            SimpleNamespace(
                returncode=0,
                stdout=" M RAW_PRIVATE_FILE_NAME.md\n",
                stderr="",
            ),
        )
    )
    monkeypatch.setattr(
        local_chat_probe.subprocess,
        "run",
        lambda *_args, **_kwargs: next(results),
    )

    provenance = local_chat_probe._source_provenance(Path("local-repo"))

    assert provenance == {
        "source_commit": "a" * 40,
        "source_tree_clean_at_probe_start": False,
    }
    assert "RAW_PRIVATE_FILE_NAME" not in json.dumps(provenance)
