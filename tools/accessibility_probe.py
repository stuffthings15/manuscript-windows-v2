from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ECHOLINK_DISABLE_HOTKEY", "1")
os.environ.setdefault("ECHOLINK_DISABLE_TRAY", "1")
os.environ.setdefault("ECHOLINK_DISABLE_SOUNDS", "1")
os.environ.setdefault("ECHOLINK_DISABLE_MEDIA_CONTROL", "1")

from PySide6.QtWidgets import (  # noqa: E402
    QAbstractButton,
    QApplication,
    QLabel,
    QScrollArea,
    QWidget,
)

from echo_link_app.echo_bar import EchoBar  # noqa: E402
from echo_link_app.settings_dialog import SettingsDialog  # noqa: E402
from echo_link_app.theme import apply_dark_theme  # noqa: E402
from echo_link_app.window import EchoLinkWindow  # noqa: E402
from echo_link_core.models import SessionState  # noqa: E402
from echo_link_core.storage import EchoLinkStore  # noqa: E402


def _visible_geometry_issues(root: QWidget) -> list[str]:
    issues: list[str] = []
    for widget in root.findChildren(QWidget):
        if not widget.isVisible():
            continue
        parent = widget.parentWidget()
        scroll_content = (
            isinstance(parent.parentWidget(), QScrollArea)
            if parent is not None and parent.parentWidget() is not None
            else False
        )
        if (
            parent is not None
            and not scroll_content
            and not parent.rect().contains(widget.geometry())
        ):
            issues.append(f"{type(widget).__name__} is outside {type(parent).__name__}")
        if isinstance(widget, QAbstractButton) and widget.text():
            text_width = widget.fontMetrics().horizontalAdvance(widget.text().replace("&", ""))
            if text_width > widget.contentsRect().width():
                issues.append(f"button text clipped: {widget.text()}")
        if isinstance(widget, QLabel) and widget.text() and not widget.wordWrap():
            if widget.fontMetrics().horizontalAdvance(widget.text()) > widget.contentsRect().width():
                issues.append(f"label text clipped: {widget.text()[:40]}")
    return issues


def run_probe() -> dict[str, object]:
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    apply_dark_theme(app)

    with tempfile.TemporaryDirectory(prefix="echo-link-accessibility-") as directory:
        window = EchoLinkWindow(
            EchoLinkStore(Path(directory) / "echo-link.db"),
            enable_hotkey=False,
            enable_tray=False,
        )
        document = window.document_workspace.create_document(
            "Accessibility check",
            "Local document text for scale verification.",
        )
        window.document_panel.refresh_documents(select_id=document.id)
        window.resize(720, 540)
        window.show()
        app.processEvents()

        issues: list[str] = []
        document_views_checked = 0
        viewports = ((720, 540), (900, 720), (1024, 700))
        for width, height in viewports:
            window.resize(width, height)
            app.processEvents()
            if window.section_nav.verticalScrollBar().maximum() != 0:
                issues.append(f"{width}x{height}: section navigation requires scrolling")
            for index in range(window.tabs.count()):
                window.tabs.setCurrentIndex(index)
                app.processEvents()
                issues.extend(
                    f"{width}x{height}/{window.tabs.tabText(index)}: {issue}"
                    for issue in _visible_geometry_issues(window)
                )
                if width == 1024 and window.tabs.tabText(index) == "Documents":
                    for view_index in range(window.document_panel.view_tabs.count()):
                        window.document_panel.view_tabs.setCurrentIndex(view_index)
                        app.processEvents()
                        document_views_checked += 1
                        issues.extend(
                            f"Documents/{window.document_panel.view_tabs.tabText(view_index)}: {issue}"
                            for issue in _visible_geometry_issues(window.document_panel)
                        )
                    window.document_panel.view_tabs.setCurrentIndex(0)

        dialog = SettingsDialog(window.store, window.engine, window)
        dialog.resize(660, 520)
        dialog.show()
        app.processEvents()
        scroll = dialog.findChild(QScrollArea)
        if scroll is None or scroll.horizontalScrollBar().maximum() != 0:
            issues.append("Settings require horizontal scrolling")

        bar = EchoBar()
        bar.show_state(SessionState.LISTENING, "Hands-free listening - Esc cancels")
        app.processEvents()
        screen = app.primaryScreen().availableGeometry()
        if not screen.contains(bar.frameGeometry()):
            issues.append("Manuscript Bar is outside the available screen")

        required_names = {
            "main": window.accessibleName(),
            "tabs": window.tabs.accessibleName(),
            "dictation_status": window.status_text.accessibleName(),
            "writing_language": window.writing_language_edit.accessibleName(),
            "document_workspace": window.document_panel.accessibleName(),
            "document_picker": window.document_panel.document_combo.accessibleName(),
            "document_title": window.document_panel.title_edit.accessibleName(),
            "document_editor": window.document_panel.editor.accessibleName(),
            "document_revisions": window.document_panel.revision_table.accessibleName(),
            "vocabulary_table": window.vocabulary_table.accessibleName(),
            "recovery_table": window.recovery_table.accessibleName(),
            "settings_microphone": dialog.microphone_combo.accessibleName(),
            "settings_hotkey": dialog.hotkey_edit.accessibleName(),
            "echo_bar": bar.accessibleName(),
        }
        empty_names = [name for name, value in required_names.items() if not value.strip()]
        issues.extend(f"missing accessible name: {name}" for name in empty_names)

        result = {
            "ok": not issues,
            "scale": os.environ.get("QT_SCALE_FACTOR", "1"),
            "device_pixel_ratio": window.devicePixelRatioF(),
            "tabs_checked": window.tabs.count(),
            "section_names": [
                window.tabs.tabText(index) for index in range(window.tabs.count())
            ],
            "viewports_checked": len(viewports),
            "document_views_checked": document_views_checked,
            "issues": issues,
        }
        bar.close()
        dialog.close()
        window.close()
        return result


if __name__ == "__main__":
    payload = run_probe()
    print(json.dumps(payload, sort_keys=True))
    raise SystemExit(0 if payload["ok"] else 1)
