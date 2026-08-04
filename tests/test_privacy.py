from __future__ import annotations

import importlib.util
import json
import socket
import time
from pathlib import Path

import numpy as np

from echo_link_core.correction import CorrectionPipeline
from echo_link_core.engine import DictationEngine
from echo_link_core.documents import DocumentWorkspace
from echo_link_core.local_chat import ApprovedSourceChat
from echo_link_core.local_sources import LocalSourceIndex
from echo_link_core.models import SessionState
from echo_link_core.storage import EchoLinkStore


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "privacy_check.py"
SPEC = importlib.util.spec_from_file_location("privacy_check", MODULE_PATH)
assert SPEC and SPEC.loader
PRIVACY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PRIVACY)


class OfflineRecorder:
    def start(self, *_args, **_kwargs):
        pass

    def stop(self):
        return np.ones(8000, dtype=np.int16)

    def cancel(self):
        pass


class OfflineTranscriber:
    def transcribe(self, *_args, **_kwargs):
        return "hello offline world"

    def update_settings(self, _settings):
        pass

    def release_model(self):
        pass


class OfflineInserter:
    def __init__(self):
        self.values = []

    def insert(self, text):
        self.values.append(text)
        return object()

    def insert_into(self, _window, text):
        return self.insert(text)

    def undo_last(self):
        return False


def test_fast_dictation_completes_with_network_blocked_and_history_off(monkeypatch, tmp_path):
    def blocked_connect(*_args, **_kwargs):
        raise AssertionError("Network access is forbidden")

    monkeypatch.setattr(socket.socket, "connect", blocked_connect)
    store = EchoLinkStore(tmp_path / "echo-link.db")
    settings = store.get_settings()
    settings.keep_history = False
    store.save_settings(settings)
    inserter = OfflineInserter()
    engine = DictationEngine(
        store,
        recorder=OfflineRecorder(),
        transcriber=OfflineTranscriber(),
        correction=CorrectionPipeline(store.vocabulary_map, store.get_settings()),
        inserter=inserter,
    )
    assert engine.start_recording()
    assert engine.stop_recording()
    deadline = time.monotonic() + 2
    while engine.state not in {SessionState.COMPLETE, SessionState.ERROR} and time.monotonic() < deadline:
        time.sleep(0.01)
    assert engine.state is SessionState.COMPLETE
    assert inserter.values == ["Hello offline world. "]
    assert store.list_history() == []


def test_source_has_no_hosted_ai_imports_or_unapproved_non_local_endpoints():
    assert PRIVACY.scan_source() == []


def test_learning_schema_stores_only_local_phrase_pairs(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    store.add_learned_correction("teh", "the")
    with store._connect() as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(learned_corrections)").fetchall()
        }
    assert {
        "raw_text",
        "final_text",
        "transcript",
        "audio",
        "audio_path",
        "window_title",
        "application_name",
    }.isdisjoint(columns)
    assert store.list_history() == []


def test_approved_source_chat_does_not_persist_questions_answers_or_sources(
    monkeypatch,
    tmp_path,
):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    source = tmp_path / "approved.md"
    source.write_text("PRIVATE_SOURCE_SENTINEL stays local.\n", encoding="utf-8")
    sources = LocalSourceIndex()
    assert sources.approve([source])[0].indexed

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit=None):
            return json.dumps({"response": "PRIVATE_ANSWER_SENTINEL [S1]."}).encode()

    monkeypatch.setattr(
        "echo_link_core.local_transport._open_request",
        lambda *_args, **_kwargs: Response(),
    )
    result = ApprovedSourceChat().ask(
        "What stays local? PRIVATE_QUESTION_SENTINEL",
        sources=sources,
        model="local-test-model",
    )
    assert result.answer.startswith("PRIVATE_ANSWER_SENTINEL")

    with store._connect() as connection:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            if not str(row[0]).startswith("sqlite_")
        ]
        database_dump = "\n".join(
            repr(tuple(row))
            for table in tables
            for row in connection.execute(f'SELECT * FROM "{table}"').fetchall()
        )
    for sentinel in (
        "PRIVATE_SOURCE_SENTINEL",
        "PRIVATE_QUESTION_SENTINEL",
        "PRIVATE_ANSWER_SENTINEL",
        str(source.resolve()),
    ):
        assert sentinel not in database_dump


def test_document_schema_has_no_audio_or_remote_location_fields(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    document = DocumentWorkspace(store).create_document("Local notes", "Private local text")
    assert document.current_revision == 1
    forbidden = {
        "audio",
        "audio_path",
        "recording",
        "recording_path",
        "remote_url",
        "source_path",
        "network_path",
        "endpoint",
    }
    with store._connect() as connection:
        for table in ("documents", "document_revisions"):
            columns = {
                str(row[1]).casefold()
                for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            assert forbidden.isdisjoint(columns)
