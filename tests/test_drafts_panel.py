from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication

from echo_link_app.drafts_panel import DraftsPanel
from echo_link_app.theme import apply_dark_theme
from echo_link_app.window import EchoLinkWindow
from echo_link_core.documents import DocumentWorkspace
from echo_link_core.drafting import DraftTemplate
from echo_link_core.storage import EchoLinkStore


@pytest.fixture(scope="module")
def app():
    os.environ["ECHOLINK_DISABLE_SOUNDS"] = "1"
    os.environ["ECHOLINK_DISABLE_MEDIA_CONTROL"] = "1"
    application = QApplication.instance() or QApplication([])
    application.setStyle("Fusion")
    apply_dark_theme(application)
    return application


def _wait(app: QApplication, predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for Drafts panel result.")


class _DraftAssistant:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.worker_thread_id: int | None = None

    def generate(self, template, **kwargs):
        self.worker_thread_id = threading.get_ident()
        self.calls.append({"template": template, **kwargs})
        return SimpleNamespace(text="Subject: Echo-Link update\n\nFacts stayed local.")


def _panel(tmp_path: Path, assistant) -> tuple[DraftsPanel, DocumentWorkspace]:
    workspace = DocumentWorkspace(EchoLinkStore(tmp_path / "echo-link.db"))
    panel = DraftsPanel(
        workspace,
        model_provider=lambda: "qwen2.5:3b",
        protected_terms_provider=lambda: ("Echo-Link", "Q3"),
        timeout_provider=lambda: 45.0,
        assistant=assistant,
    )
    return panel, workspace


def test_drafts_panel_lists_all_templates_and_runs_on_worker(app, tmp_path):
    assistant = _DraftAssistant()
    panel, workspace = _panel(tmp_path, assistant)
    main_thread_id = threading.get_ident()

    assert [panel.template_combo.itemText(index) for index in range(panel.template_combo.count())] == [
        template.value for template in DraftTemplate
    ]
    assert "LOCAL ONLY" in panel.local_status.text()
    assert "AI runs on this computer" in panel.local_status.text()
    assert "qwen" not in panel.local_status.text().casefold()
    assert "Review required" in panel.local_status.text()
    assert not panel.save_button.isEnabled()
    assert not panel.stage_button.isEnabled()
    assert workspace.list_documents() == ()

    panel.facts_edit.setPlainText("Echo-Link Q3 update. Facts stayed local.")
    panel.direction_edit.setPlainText("Concise and neutral")
    panel.generate()
    assert not panel.generate_button.isEnabled()
    _wait(app, lambda: panel.result_edit.toPlainText() != "")

    assert assistant.worker_thread_id != main_thread_id
    assert assistant.calls[0]["model"] == "qwen2.5:3b"
    assert assistant.calls[0]["protected_terms"] == ("Echo-Link", "Q3")
    assert assistant.calls[0]["timeout"] == 45.0
    assert panel.save_button.isEnabled()
    assert panel.stage_button.isEnabled()
    assert workspace.list_documents() == ()
    panel.close()


def test_draft_use_is_explicit_and_private(app, tmp_path):
    panel, workspace = _panel(tmp_path, _DraftAssistant())
    staged: list[str] = []
    panel.stage_private_paste_requested.connect(staged.append)
    panel.result_edit.setPlainText("Reviewed local draft.")

    panel.stage_private_paste()
    assert staged == ["Reviewed local draft."]
    assert workspace.list_documents() == ()
    assert "Windows clipboard unchanged" in panel.status.text()

    panel.save_to_documents()
    documents = workspace.list_documents()
    assert len(documents) == 1
    assert documents[0].content == "Reviewed local draft."
    assert documents[0].title.startswith("Email draft")
    assert not panel.save_button.isEnabled()
    assert staged == ["Reviewed local draft."]

    panel.reject()
    assert panel.result_edit.toPlainText() == ""
    assert len(workspace.list_documents()) == 1
    assert "Nothing was saved, inserted, or sent" in panel.status.text()
    panel.close()


def test_reject_discards_late_worker_result(app, tmp_path):
    started = threading.Event()
    release = threading.Event()

    class SlowAssistant:
        def generate(self, *_args, **_kwargs):
            started.set()
            assert release.wait(timeout=2.0)
            return SimpleNamespace(text="This stale draft must not appear.")

    panel, workspace = _panel(tmp_path, SlowAssistant())
    panel.facts_edit.setPlainText("Facts for a delayed request.")
    panel.generate()
    assert started.wait(timeout=1.0)
    panel.reject()
    release.set()
    _wait(app, lambda: panel.generate_button.isEnabled())
    for _ in range(5):
        app.processEvents()
        time.sleep(0.01)

    assert panel.result_edit.toPlainText() == ""
    assert workspace.list_documents() == ()
    assert "Draft rejected" in panel.status.text()
    panel.close()


def test_main_window_exposes_accessible_drafts_section(app, tmp_path):
    window = EchoLinkWindow(
        EchoLinkStore(tmp_path / "window.db"),
        enable_hotkey=False,
        enable_tray=False,
    )
    window.resize(1024, 760)
    window.show()
    window._show_tab("Drafts")
    app.processEvents()

    assert window.tabs.currentWidget() is window.drafts_panel
    assert window.drafts_panel.accessibleName() == "Local draft generation"
    for control in (
        window.drafts_panel.template_combo,
        window.drafts_panel.facts_edit,
        window.drafts_panel.direction_edit,
        window.drafts_panel.generate_button,
        window.drafts_panel.result_edit,
        window.drafts_panel.save_button,
        window.drafts_panel.stage_button,
        window.drafts_panel.reject_button,
    ):
        assert control.accessibleName()
    assert window.main_scroll.horizontalScrollBar().maximum() == 0

    screenshot_dir = os.environ.get("ECHOLINK_SCREENSHOT_DIR")
    if screenshot_dir:
        destination = Path(screenshot_dir)
        destination.mkdir(parents=True, exist_ok=True)
        assert window.grab().save(str(destination / "echo-link-drafts.png"))
    window.main_scroll.verticalScrollBar().setValue(
        window.main_scroll.verticalScrollBar().maximum()
    )
    app.processEvents()
    for button in (
        window.drafts_panel.save_button,
        window.drafts_panel.stage_button,
        window.drafts_panel.reject_button,
    ):
        top_left = button.mapTo(window.main_scroll.viewport(), QPoint(0, 0))
        assert window.main_scroll.viewport().rect().contains(QRect(top_left, button.size()))
    if screenshot_dir:
        assert window.grab().save(str(destination / "echo-link-drafts-actions.png"))
    window.close()
