from __future__ import annotations

import os
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from echo_link_app.theme import apply_dark_theme
from echo_link_app.window import EchoLinkWindow
from echo_link_core.storage import EchoLinkStore


@pytest.fixture(scope="module")
def app():
    os.environ["ECHOLINK_DISABLE_SOUNDS"] = "1"
    os.environ["ECHOLINK_DISABLE_MEDIA_CONTROL"] = "1"
    application = QApplication.instance() or QApplication([])
    application.setStyle("Fusion")
    apply_dark_theme(application)
    return application


def _window(tmp_path: Path, name: str = "echo-link.db") -> EchoLinkWindow:
    return EchoLinkWindow(
        EchoLinkStore(tmp_path / name),
        enable_hotkey=False,
        enable_tray=False,
    )


def _close_without_prompt(window: EchoLinkWindow) -> None:
    window.document_panel._dirty = False
    window.close()


def test_new_edit_save_and_unchanged_save_does_not_add_revision(app, tmp_path):
    window = _window(tmp_path)
    panel = window.document_panel

    panel._new_document()

    document_id = panel.current_document_id
    assert document_id is not None
    assert panel.loaded_revision == 1
    assert panel.title_edit.text() == "Untitled document"
    assert panel.revision_table.rowCount() == 1
    assert panel.save_button.isEnabled() is False

    panel.title_edit.setText("Project Notes")
    panel.editor.setPlainText("First local draft.\n")

    assert panel.is_dirty is True
    assert panel.save_button.isEnabled() is True
    assert "Unsaved changes" in panel.status.text()
    assert panel.save_document() is True

    saved = panel.workspace.open_document(document_id)
    assert saved.title == "Project Notes"
    assert saved.content == "First local draft.\n"
    assert saved.current_revision == 2
    assert panel.is_dirty is False
    assert panel.save_button.isEnabled() is False
    assert [item.revision_number for item in panel.workspace.list_revisions(document_id)] == [2, 1]

    assert panel.save_document() is True
    assert panel.workspace.open_document(document_id).current_revision == 2
    assert [item.revision_number for item in panel.workspace.list_revisions(document_id)] == [2, 1]
    _close_without_prompt(window)


def test_dirty_document_switch_honors_cancel_and_discard(app, tmp_path, monkeypatch):
    window = _window(tmp_path)
    panel = window.document_panel
    first = panel.workspace.create_document("First", "One")
    second = panel.workspace.create_document("Second", "Two")
    panel.refresh_documents(select_id=first.id)
    panel.editor.setPlainText("Unsaved first draft")
    assert panel.is_dirty

    prompts: list[str] = []

    def cancel(_parent, _title, text, *_args, **_kwargs):
        prompts.append(text)
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QMessageBox, "question", cancel)
    panel.document_combo.setCurrentIndex(panel.document_combo.findData(second.id))

    assert panel.current_document_id == first.id
    assert panel.document_combo.currentData() == first.id
    assert panel.is_dirty
    assert any("Open another document" in prompt for prompt in prompts)

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Discard,
    )
    panel.document_combo.setCurrentIndex(panel.document_combo.findData(second.id))

    assert panel.current_document_id == second.id
    assert panel.editor.toPlainText() == "Two"
    assert panel.is_dirty is False
    assert panel.workspace.open_document(first.id).content == "One"
    _close_without_prompt(window)


def test_stale_editor_save_is_rejected_without_overwrite(app, tmp_path, monkeypatch):
    window = _window(tmp_path)
    panel = window.document_panel
    document = panel.workspace.create_document("Shared", "Revision one")
    panel.refresh_documents(select_id=document.id)
    panel.editor.setPlainText("Stale editor change")

    panel.workspace.update_document(
        document.id,
        "External revision two",
        expected_revision=1,
    )
    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, text, *_args, **_kwargs: warnings.append(text),
    )

    assert panel.save_document() is False

    current = panel.workspace.open_document(document.id)
    assert current.current_revision == 2
    assert current.content == "External revision two"
    assert [item.revision_number for item in panel.workspace.list_revisions(document.id)] == [2, 1]
    assert any("changed after it was opened" in warning for warning in warnings)
    _close_without_prompt(window)


def test_dirty_export_requires_save_and_exports_visible_content(
    app,
    tmp_path,
    monkeypatch,
):
    window = _window(tmp_path)
    panel = window.document_panel
    document = panel.workspace.create_document("Export", "Saved content")
    panel.refresh_documents(select_id=document.id)
    panel.editor.setPlainText("Visible unsaved content")
    destination = tmp_path / "exported.txt"
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(destination), "Text document (*.txt)"),
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Cancel,
    )

    panel._export_document()

    assert destination.exists() is False
    assert panel.is_dirty is True
    assert panel.workspace.open_document(document.id).current_revision == 1

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Save,
    )
    panel._export_document()

    assert destination.read_text(encoding="utf-8") == "Visible unsaved content"
    assert panel.workspace.open_document(document.id).current_revision == 2
    assert panel.is_dirty is False
    assert "Exported locally" in panel.status.text()
    _close_without_prompt(window)


def test_revision_preview_and_restore_append_new_revision(app, tmp_path, monkeypatch):
    window = _window(tmp_path)
    panel = window.document_panel
    document = panel.workspace.create_document("Draft", "Version one")
    panel.workspace.update_document(
        document.id,
        "Version two",
        title="Draft two",
        expected_revision=1,
    )
    panel.refresh_documents(select_id=document.id)
    panel.view_tabs.setCurrentIndex(1)
    panel.revision_table.selectRow(1)
    app.processEvents()

    assert panel._selected_revision_number() == 1
    assert panel.revision_preview.toPlainText() == "Version one"
    assert panel.revision_preview.isReadOnly()
    assert panel.restore_button.isEnabled()

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    panel._restore_revision()

    restored = panel.workspace.open_document(document.id)
    revisions = panel.workspace.list_revisions(document.id)
    assert restored.current_revision == 3
    assert restored.title == "Draft"
    assert restored.content == "Version one"
    assert [item.revision_number for item in revisions] == [3, 2, 1]
    assert revisions[0].restored_from_revision == 1
    assert panel.workspace.read_revision(document.id, 2).content == "Version two"
    assert panel.workspace.read_revision(document.id, 1).content == "Version one"
    assert "as new revision 3" in panel.status.text()
    _close_without_prompt(window)


def test_real_quit_is_blocked_until_dirty_document_is_resolved(app, tmp_path, monkeypatch):
    window = _window(tmp_path)
    panel = window.document_panel
    panel._new_document()
    panel.editor.setPlainText("Unsaved before quit")
    window.show()
    app.processEvents()

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Cancel,
    )
    window.close()
    app.processEvents()

    assert window.isVisible()
    assert window._shutdown_complete is False
    assert panel.is_dirty is True

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Discard,
    )
    window.close()
    app.processEvents()

    assert window.isVisible() is False
    assert window._shutdown_complete is True
