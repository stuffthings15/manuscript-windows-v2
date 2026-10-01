from __future__ import annotations

import os
import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from echo_link_app.documents_panel import DocumentEditorPanel
from echo_link_app.theme import apply_dark_theme
from echo_link_core.documents import DocumentWorkspace
from echo_link_core.selected_text import COMMANDS
from echo_link_core.storage import EchoLinkStore


@pytest.fixture(scope="module")
def app():
    os.environ["ECHOLINK_DISABLE_SOUNDS"] = "1"
    application = QApplication.instance() or QApplication([])
    application.setStyle("Fusion")
    apply_dark_theme(application)
    return application


class FakeAssistant:
    endpoint = "http://127.0.0.1:11434"

    def __init__(
        self,
        *,
        result: str = "Clean result.",
        gate=None,
        started=None,
        error: Exception | None = None,
    ):
        self.result = result
        self.gate = gate
        self.started = started
        self.error = error
        self.calls = []

    def run(self, text: str, **options):
        self.calls.append((text, options))
        if self.started is not None:
            self.started.set()
        if self.gate is not None:
            assert self.gate.wait(2.0)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(text=self.result)


def _panel(tmp_path, assistant: FakeAssistant) -> DocumentEditorPanel:
    workspace = DocumentWorkspace(EchoLinkStore(tmp_path / "echo-link.db"))
    panel = DocumentEditorPanel(
        workspace,
        writing_assistant=assistant,
        writing_model_provider=lambda: "private-local:3b",
        protected_terms_provider=lambda: ("Echo-Link",),
        writing_timeout_provider=lambda: 12.0,
    )
    document = workspace.create_document("Draft", "Current visible draft.")
    panel.refresh_documents(select_id=document.id)
    return panel


def _wait_until(app, predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    app.processEvents()
    assert predicate()


def test_assistant_uses_current_draft_and_never_auto_applies_or_saves(app, tmp_path):
    assistant = FakeAssistant(result="Proofread local result.")
    panel = _panel(tmp_path, assistant)
    before = panel.workspace.open_document(panel.current_document_id)

    assert panel.view_tabs.tabText(3) == "Assistant"
    assert tuple(panel.assistant_command_buttons) == tuple(COMMANDS)
    panel.assistant_command_buttons["Proofread"].click()
    _wait_until(app, lambda: panel.assistant_result_edit.toPlainText() != "")

    source, options = assistant.calls[0]
    assert source == "Current visible draft."
    assert options["command"] == "Proofread"
    assert options["model"] == "private-local:3b"
    assert options["protected_terms"] == ("Echo-Link",)
    assert options["timeout"] == 12.0
    assert panel.editor.toPlainText() == "Current visible draft."
    assert panel.workspace.open_document(before.id).current_revision == before.current_revision

    panel.assistant_result_edit.setPlainText("User-edited reviewed result.")
    panel.assistant_apply_button.click()
    assert panel.editor.toPlainText() == "User-edited reviewed result."
    assert panel.is_dirty is True
    saved = panel.workspace.open_document(before.id)
    assert saved.current_revision == before.current_revision
    assert saved.content == "Current visible draft."
    assert "unsaved draft" in panel.assistant_status.text()
    panel.close()


def test_ollama_not_running_message_reaches_the_status_label_unchanged(app, tmp_path):
    """The assistant's own install message must reach the writer verbatim.

    user_ai_status() is for the bundled correction engine and hides runtime
    names on purpose; applying it here used to remap this exact message to
    the generic "could not finish this section. Retry.", which sends a
    writer with no Ollama installed toward an action that can never work.
    """
    from echo_link_core.selected_text import OLLAMA_NOT_RUNNING_MESSAGE

    assistant = FakeAssistant(error=RuntimeError(OLLAMA_NOT_RUNNING_MESSAGE))
    panel = _panel(tmp_path, assistant)

    panel.assistant_command_buttons["Proofread"].click()
    _wait_until(app, lambda: panel.assistant_status.text() != "Running Proofread locally...")

    assert panel.assistant_status.text() == OLLAMA_NOT_RUNNING_MESSAGE
    assert "ollama" in panel.assistant_status.text().casefold()
    panel.close()


def test_stage_is_signal_only_and_reject_never_changes_draft(
    app, tmp_path, monkeypatch
):
    assistant = FakeAssistant(result="Reviewed result.")
    panel = _panel(tmp_path, assistant)
    staged = []
    panel.assistant_stage_requested.connect(staged.append)
    monkeypatch.setattr(
        QApplication,
        "clipboard",
        staticmethod(lambda: pytest.fail("Windows clipboard must not be used")),
    )

    panel.assistant_command_buttons["Friendly"].click()
    _wait_until(app, lambda: panel.assistant_result_edit.toPlainText() != "")
    panel.assistant_result_edit.setPlainText("Edited private result.")
    panel.assistant_stage_button.click()
    assert staged == ["Edited private result."]
    assert panel.editor.toPlainText() == "Current visible draft."
    assert "clipboard unchanged" in panel.assistant_status.text()

    panel.assistant_reject_button.click()
    assert panel.assistant_result_edit.toPlainText() == ""
    assert panel.editor.toPlainText() == "Current visible draft."
    assert "not changed" in panel.assistant_status.text()
    panel.close()


def test_draft_change_discards_late_worker_result(app, tmp_path):
    gate = threading.Event()
    started = threading.Event()
    assistant = FakeAssistant(result="Late stale result.", gate=gate, started=started)
    panel = _panel(tmp_path, assistant)

    panel.assistant_command_buttons["Rewrite"].click()
    assert started.wait(1.0)
    panel.editor.setPlainText("A newer visible draft.")
    gate.set()
    _wait_until(app, lambda: not panel._assistant_busy)
    app.processEvents()

    assert panel.assistant_result_edit.toPlainText() == ""
    assert panel.editor.toPlainText() == "A newer visible draft."
    assert "Draft changed" in panel.assistant_status.text()
    panel.close()


def test_non_loopback_injected_assistant_is_blocked(app, tmp_path):
    assistant = FakeAssistant()
    assistant.endpoint = "https://example.com"
    panel = _panel(tmp_path, assistant)

    panel.assistant_command_buttons["Proofread"].click()
    app.processEvents()

    assert assistant.calls == []
    assert "numeric loopback" in panel.assistant_status.text()
    panel.close()


def test_document_assistant_has_no_unapproved_fallback_model(app, tmp_path):
    assistant = FakeAssistant()
    workspace = DocumentWorkspace(EchoLinkStore(tmp_path / "echo-link.db"))
    panel = DocumentEditorPanel(workspace, writing_assistant=assistant)
    document = workspace.create_document("Draft", "Current visible draft.")
    panel.refresh_documents(select_id=document.id)

    panel.assistant_command_buttons["Proofread"].click()
    app.processEvents()

    assert assistant.calls == []
    assert "unavailable" in panel.assistant_status.text().casefold()
    panel.close()
