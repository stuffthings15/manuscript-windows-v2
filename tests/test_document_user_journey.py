"""Real Qt controls and database; offscreen, not Windows desktop acceptance."""
import pytest

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from echo_link_app.documents_panel import DocumentEditorPanel
from echo_link_core.documents import DocumentWorkspace
from echo_link_core.storage import EchoLinkStore


def test_writer_creates_types_saves_and_restores_with_controls(tmp_path):
    app = QApplication.instance() or QApplication([])
    workspace = DocumentWorkspace(EchoLinkStore(tmp_path / "journey.db"))
    panel = DocumentEditorPanel(workspace)
    panel.resize(1100, 900)
    panel.show()
    app.processEvents()
    try:
        QTest.mouseClick(panel.new_button, Qt.MouseButton.LeftButton)
        document_id = panel.current_document_id
        assert document_id is not None
        QTest.mouseClick(panel.editor.viewport(), Qt.MouseButton.LeftButton)
        QTest.keyClicks(panel.editor, "Original sentence.")
        QTest.mouseClick(panel.save_button, Qt.MouseButton.LeftButton)
        assert workspace.open_document(document_id).content == "Original sentence."
        QTest.keyClick(panel.editor, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClicks(panel.editor, "Revised sentence.")
        QTest.mouseClick(panel.save_button, Qt.MouseButton.LeftButton)
        assert workspace.open_document(document_id).current_revision == 3

        bar = panel.view_tabs.tabBar()
        QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(1).center())
        app.processEvents()
        item = panel.revision_table.item(1, 0)
        QTest.mouseClick(panel.revision_table.viewport(), Qt.MouseButton.LeftButton,
                         pos=panel.revision_table.visualItemRect(item).center())
        assert panel.revision_preview.toPlainText() == "Original sentence."
        answered = []
        def accept_dialog():
            dialog = app.activeModalWidget()
            if isinstance(dialog, QMessageBox):
                button = dialog.button(QMessageBox.StandardButton.Yes)
                if button is not None:
                    answered.append(True)
                    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                else:
                    dialog.reject()
        QTimer.singleShot(100, accept_dialog)
        QTest.mouseClick(panel.restore_button, Qt.MouseButton.LeftButton)
        assert answered == [True]
        restored = workspace.open_document(document_id)
        assert restored.content == "Original sentence."
        assert restored.current_revision == 4
        assert [r.content for r in workspace.list_revisions(document_id)] == [
            "Original sentence.", "Revised sentence.", "Original sentence.", "",
        ]
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("choice", [QMessageBox.StandardButton.Save,
    QMessageBox.StandardButton.Discard, QMessageBox.StandardButton.Cancel])
def test_unsaved_dialog_honors_the_actual_button(tmp_path, choice):
    app = QApplication.instance() or QApplication([])
    workspace = DocumentWorkspace(EchoLinkStore(tmp_path / "unsaved.db"))
    panel = DocumentEditorPanel(workspace)
    panel.resize(1100, 900)
    panel.show()
    app.processEvents()
    try:
        QTest.mouseClick(panel.new_button, Qt.MouseButton.LeftButton)
        original_id = panel.current_document_id
        QTest.mouseClick(panel.editor.viewport(), Qt.MouseButton.LeftButton)
        QTest.keyClicks(panel.editor, "Unsaved writing.")
        answered = []
        def answer_dialog():
            dialog = app.activeModalWidget()
            if isinstance(dialog, QMessageBox):
                button = dialog.button(choice)
                if button is not None:
                    answered.append(choice)
                    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                else:
                    dialog.reject()
        QTimer.singleShot(100, answer_dialog)
        QTest.mouseClick(panel.new_button, Qt.MouseButton.LeftButton)
        assert answered == [choice]
        saved = workspace.open_document(original_id)
        if choice == QMessageBox.StandardButton.Cancel:
            assert panel.current_document_id == original_id
            assert panel.editor.toPlainText() == "Unsaved writing."
            assert saved.content == ""
        else:
            assert panel.current_document_id != original_id
            assert saved.content == ("Unsaved writing." if choice == QMessageBox.StandardButton.Save else "")
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()
