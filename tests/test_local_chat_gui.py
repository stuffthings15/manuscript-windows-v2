from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from echo_link_app.theme import apply_dark_theme
from echo_link_app.window import EchoLinkWindow
from echo_link_core.local_chat import ChatCitation, LocalChatResult
from echo_link_core.polish_types import (
    ExecutionPlan,
    PolishMode,
    PolishTask,
    RuntimeBackend,
    RuntimeStatus,
)
from echo_link_core.storage import EchoLinkStore


@pytest.fixture(scope="module")
def app():
    os.environ["ECHOLINK_DISABLE_SOUNDS"] = "1"
    os.environ["ECHOLINK_DISABLE_MEDIA_CONTROL"] = "1"
    application = QApplication.instance() or QApplication([])
    application.setStyle("Fusion")
    apply_dark_theme(application)
    return application


def _wait_for(app: QApplication, predicate, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert predicate()


def _accept_loaded_ai(window: EchoLinkWindow) -> ExecutionPlan:
    plan = ExecutionPlan(
        policy_version=8,
        mode=PolishMode.AUTO,
        task=PolishTask.PROOFREAD,
        use_llm=True,
        pack_id="gemma4-e4b-qat-v1",
        model="gemma4:e4b-it-qat",
        backend=RuntimeBackend.CUDA,
        context_window=2048,
        max_output_tokens=512,
        require_full_gpu=True,
    )
    assert window.ai_coordinator.accept_verified(plan)

    def loaded_status(model: str, *, timeout: float = 1.0) -> RuntimeStatus:
        del timeout
        lease = window.ai_coordinator.lease()
        assert lease is not None and lease.plan == plan
        assert model == plan.model
        return RuntimeStatus(
            model=model,
            loaded=True,
            backend=plan.backend,
            model_bytes=4_000,
            gpu_bytes=4_000,
        )

    window.ai_coordinator.status = loaded_status
    return plan


def test_sources_chat_runs_async_with_current_model_and_exact_citations(
    app,
    tmp_path: Path,
):
    source = tmp_path / "approved.md"
    source.write_text(
        "Privacy notes\nSpeech stays on this computer.\n",
        encoding="utf-8",
    )
    store = EchoLinkStore(tmp_path / "echo-link.db")
    window = EchoLinkWindow(store, enable_hotkey=False)
    plan = _accept_loaded_ai(window)
    assert window.local_source_index.approve([source])[0].indexed
    window._refresh_local_source_state()

    path = str(source.resolve())
    started = threading.Event()
    release = threading.Event()
    captured = {}

    def fake_ask(question, *, sources, model, timeout):
        captured.update(
            question=question,
            sources=sources,
            model=model,
            timeout=timeout,
        )
        started.set()
        assert release.wait(2.0)
        return LocalChatResult(
            "Speech stays local [S1].",
            model,
            (ChatCitation("S1", path, 2, "Speech stays on this computer."),),
        )

    window.source_chat.ask = fake_ask
    window.source_query.setPlainText("speech stays")
    window._search_local_sources()
    search_before = window.source_results.toPlainText()
    window.source_chat_question.setPlainText("Where does speech stay?")
    window._ask_local_sources()

    assert started.wait(1.0)
    assert not window.source_chat_button.isEnabled()
    assert not window.source_add_button.isEnabled()
    assert not window.source_clear_button.isEnabled()
    assert "on this computer" in window.source_chat_state.text()
    release.set()
    _wait_for(app, window.source_chat_button.isEnabled)

    assert captured["question"] == "Where does speech stay?"
    assert captured["sources"] is window.local_source_index
    assert captured["model"] == plan.model
    assert captured["timeout"] >= 30.0
    assert window.source_add_button.isEnabled()
    assert window.source_clear_button.isEnabled()
    answer = window.source_chat_answer.toPlainText()
    assert "Speech stays local [S1]." in answer
    assert f"[S1] {path}:2" in answer
    assert "Speech stays on this computer." in answer
    assert "Session only" in window.source_chat_state.text()
    assert window.source_results.toPlainText() == search_before
    assert window.source_similarity_button.isEnabled()
    assert window.source_chat_answer.isReadOnly()
    assert window.source_chat_question.accessibleName() == "Question for approved local sources"
    assert window.source_chat_button.accessibleName() == "Ask approved local sources"
    assert window.source_chat_answer.accessibleName() == "Local source chat answer and citations"

    window._clear_local_sources()
    assert window.local_source_index.approved_paths == ()
    assert window.source_chat_question.toPlainText() == ""
    assert window.source_chat_answer.toPlainText() == ""
    window.close()


def test_sources_chat_validates_locally_and_recovers_from_model_error(
    app,
    tmp_path: Path,
):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    window._ask_local_sources()
    assert "Enter a question" in window.source_chat_state.text()

    window.source_chat_question.setPlainText("What is private?")
    window._ask_local_sources()
    assert "Approve at least one" in window.source_chat_state.text()

    source = tmp_path / "approved.txt"
    source.write_text("Private processing stays local.\n", encoding="utf-8")
    window.local_source_index.approve([source])
    _accept_loaded_ai(window)

    def fail_ask(*_args, **_kwargs):
        raise RuntimeError("Local source chat AI unavailable: stopped")

    window.source_chat.ask = fail_ask
    window._ask_local_sources()
    _wait_for(app, window.source_chat_button.isEnabled)

    assert "unavailable" in window.source_chat_state.text()
    assert window.source_add_button.isEnabled()
    assert window.source_clear_button.isEnabled()
    assert window.source_chat_answer.toPlainText() == ""
    assert "Nothing is saved or applied" in window.source_chat_button.accessibleDescription()
    window.close()


def test_sources_chat_refuses_an_unaccepted_persisted_model(app, tmp_path: Path):
    source = tmp_path / "approved.txt"
    source.write_text("Private processing stays local.\n", encoding="utf-8")
    store = EchoLinkStore(tmp_path / "echo-link.db")
    settings = store.get_settings()
    settings.ollama_model = "unaccepted-local:7b"
    settings.polish_model_pack_id = "unaccepted-pack"
    store.save_settings(settings)
    window = EchoLinkWindow(store, enable_hotkey=False)
    assert window.local_source_index.approve([source])[0].indexed

    def forbidden_ask(*_args, **_kwargs):
        raise AssertionError("Unaccepted local AI must not run")

    window.source_chat.ask = forbidden_ask
    window.source_chat_question.setPlainText("What is private?")
    window._ask_local_sources()

    assert window.source_chat_button.isEnabled()
    assert "AI is still getting ready" in window.source_chat_state.text()
    assert window.engine.settings.ollama_model == ""
    window.close()
