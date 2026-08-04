from __future__ import annotations

import os
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from echo_link_app.documents_panel import DocumentEditorPanel
from echo_link_app.theme import apply_dark_theme
from echo_link_core.documents import DocumentWorkspace
from echo_link_core.storage import EchoLinkStore


@pytest.fixture(scope="module")
def app():
    os.environ["ECHOLINK_DISABLE_SOUNDS"] = "1"
    application = QApplication.instance() or QApplication([])
    application.setStyle("Fusion")
    apply_dark_theme(application)
    return application


def _panel(tmp_path) -> DocumentEditorPanel:
    workspace = DocumentWorkspace(EchoLinkStore(tmp_path / "echo-link.db"))
    return DocumentEditorPanel(workspace)


def test_analytics_tab_refreshes_on_load_explicit_analyze_and_save(app, tmp_path):
    panel = _panel(tmp_path)
    document = panel.workspace.create_document(
        "Local draft",
        "First paragraph is clear.\n\nSecond paragraph is also direct.",
    )
    panel.refresh_documents(select_id=document.id)

    assert panel.view_tabs.count() == 4
    assert panel.view_tabs.tabText(2) == "Analytics"
    assert panel.view_tabs.tabText(3) == "Assistant"
    assert panel.analytics_last_report is not None
    assert panel.analytics_metrics["paragraphs"].text() == "2"

    panel.editor.setPlainText("I think this is actually very unclear.")
    old_score = panel.analytics_score.text()
    panel.analytics_button.click()
    app.processEvents()
    assert panel.analytics_score.text() != "-- / 100"
    assert panel.analytics_score.text() != old_score or "I think" in panel.analytics_flags.text()
    assert "I think" in panel.analytics_flags.text()
    assert "actually" in panel.analytics_flags.text()
    assert "refreshed locally" in panel.analytics_status.text()

    assert panel.save_document() is True
    assert panel.analytics_last_report is not None
    assert panel.analytics_metrics["words"].text() == "7"
    assert panel.analytics_button.accessibleName() == "Analyze document locally"
    panel.resize(640, 640)
    panel.view_tabs.setCurrentIndex(2)
    panel.show()
    app.processEvents()
    assert panel.analytics_button.isVisible()
    assert panel.analytics_scroll.horizontalScrollBar().maximum() == 0
    needed = panel.analytics_button.fontMetrics().horizontalAdvance(
        panel.analytics_button.text()
    ) + 28
    assert panel.analytics_button.width() >= needed
    capture_dir = os.environ.get("ECHOLINK_SCREENSHOT_DIR")
    if capture_dir:
        destination = Path(capture_dir)
        destination.mkdir(parents=True, exist_ok=True)
        assert panel.grab().save(str(destination / "echo-link-document-analytics.png"))
        panel.analytics_scroll.verticalScrollBar().setValue(
            panel.analytics_scroll.verticalScrollBar().maximum()
        )
        app.processEvents()
        assert panel.grab().save(
            str(destination / "echo-link-document-analytics-guidance.png")
        )
    panel.close()


def test_analytics_empty_document_has_clear_state(app, tmp_path):
    panel = _panel(tmp_path)
    document = panel.workspace.create_document("Blank")
    panel.refresh_documents(select_id=document.id)

    assert panel.analytics_last_report is None
    assert panel.analytics_score.text() == "-- / 100"
    assert "Write or load" in panel.analytics_status.text()
    panel.close()
