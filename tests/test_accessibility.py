from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent
from PySide6.QtWidgets import QApplication, QWidget

import echo_link_app.accessibility as accessibility
from echo_link_app.settings_dialog import SettingsDialog
from echo_link_app.theme import COLORS, apply_dark_theme
from echo_link_app.window import EchoLinkWindow
from echo_link_core.models import DictationMode
from echo_link_core.storage import EchoLinkStore


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def app():
    os.environ["ECHOLINK_DISABLE_SOUNDS"] = "1"
    os.environ["ECHOLINK_DISABLE_MEDIA_CONTROL"] = "1"
    application = QApplication.instance() or QApplication([])
    application.setStyle("Fusion")
    apply_dark_theme(application)
    return application


def _focus_chain(root: QWidget) -> list[QWidget]:
    widgets: list[QWidget] = []
    current = root.nextInFocusChain()
    seen: set[QWidget] = set()
    while current not in seen:
        seen.add(current)
        if (
            current.isVisible()
            and current.isEnabled()
            and current.focusPolicy() & Qt.FocusPolicy.TabFocus
        ):
            widgets.append(current)
        current = current.nextInFocusChain()
    return widgets


def _assert_in_order(chain: list[QWidget], expected: list[QWidget]) -> None:
    positions = [chain.index(widget) for widget in expected]
    assert positions == sorted(positions)


def test_primary_controls_have_stable_accessible_names(app, tmp_path):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    expected = {
        window.tabs: "Manuscript sections",
        window.status_text: "Dictation status",
        window.private_paste_state: "Manuscript private paste status",
        window.clear_private_paste_button: "Clear Manuscript private paste",
        window.level_bar: "Microphone level",
        window.writing_command_combo: "Selected-text command",
        window.writing_language_edit: "Translation language",
        window.writing_instruction_edit: "Optional writing direction",
        window.writing_source: "Captured original text",
        window.writing_result_edit: "Selected-text command result",
        window.writing_analysis: "Readability analysis",
        window.document_panel: "Local document workspace",
        window.document_panel.document_combo: "Saved local documents",
        window.document_panel.title_edit: "Document title",
        window.document_panel.editor: "Document editor",
        window.document_panel.status: "Document status",
        window.document_panel.view_tabs: "Document editor and revisions",
        window.document_panel.revision_table: "Document revisions",
        window.document_panel.revision_preview: "Selected revision preview",
        window.vocabulary_table: "Custom vocabulary entries",
        window.snippet_table: "Saved local snippets",
        window.recovery_table: "Failed text recovery items",
        window.history_table: "Local dictation history",
        window.evaluation_notes: "Optional capstone case note",
    }
    assert {widget.accessibleName() for widget in expected} == set(expected.values())
    for widget, name in expected.items():
        assert widget.accessibleName() == name

    dialog = SettingsDialog(window.store, window.engine, window)
    form_fields = {
        dialog.microphone_combo: "Microphone",
        dialog.hotkey_edit: "Global dictation hotkey",
        dialog.hands_free_hotkey_edit: "Hands-free hotkey",
        dialog.command_hotkey_edit: "Writing HUD hotkey",
        dialog.private_paste_hotkey_edit: "Manuscript private paste hotkey",
        dialog.silence_timeout_spin: "Silence timeout",
        dialog.language_combo: "Language",
        dialog.start_sound_combo: "Start cue",
        dialog.stop_sound_combo: "Stop cue",
        dialog.output_combo: "Play through",
        dialog.mode_combo: "Default mode",
        dialog.insertion_mode_combo: "Insertion method",
        dialog.model_combo: "Speech AI",
        dialog.device_combo: "Speech device",
    }
    for widget, name in form_fields.items():
        assert widget.accessibleName() == name
    dialog.close()
    window.close()


def test_keyboard_focus_follows_visual_workflow(app, tmp_path):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    window.show()
    app.processEvents()

    dictate_chain = _focus_chain(window)
    _assert_in_order(
        dictate_chain,
        [
            window.mode_buttons[DictationMode.FAST],
            window.mode_buttons[DictationMode.DEVELOPER],
            window.mode_buttons[DictationMode.REFINE],
            window.mode_buttons[DictationMode.ACCURACY],
                window.review_check,
                window.record_button,
                window.review_tabs,
                window.result_text,
            ],
        )
    assert window.undo_button not in dictate_chain
    window.undo_button.setEnabled(True)
    enabled_chain = _focus_chain(window)
    assert window.undo_button in enabled_chain

    window.tabs.setCurrentIndex(1)
    app.processEvents()
    writing_chain = _focus_chain(window)
    _assert_in_order(
        writing_chain,
        [
            window.writing_capture_button,
            window.writing_command_combo,
            window.writing_profile_combo,
            window.writing_language_edit,
            window.writing_run_button,
            window.writing_instruction_edit,
            window.writing_analyze_button,
            window.writing_editors,
            window.writing_source,
        ],
    )
    window._handle_writing_result((True, "Edited text.", "Ready for review."))
    app.processEvents()
    result_chain = _focus_chain(window)
    _assert_in_order(
        result_chain,
        [
            window.writing_editors,
            window.writing_result_edit,
            window.writing_replace_button,
            window.writing_copy_button,
        ],
    )

    document = window.document_workspace.create_document(
        "Accessibility document",
        "Keyboard focus text.",
    )
    window.document_panel.refresh_documents(select_id=document.id)
    window._show_tab("Documents")
    app.processEvents()
    document_chain = _focus_chain(window)
    _assert_in_order(
        document_chain,
        [
            window.document_panel.new_button,
            window.document_panel.import_button,
            window.document_panel.export_button,
            window.document_panel.document_combo,
            window.document_panel.view_tabs,
            window.document_panel.title_edit,
            window.document_panel.editor,
        ],
    )
    window.close()


def test_dictation_status_emits_accessible_announcement(app, tmp_path, monkeypatch):
    events: list[QAccessibleAnnouncementEvent] = []
    monkeypatch.setattr(accessibility, "_send_accessibility_event", events.append)
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    events.clear()

    window._set_status("Microphone blocked. Open Settings.", COLORS["red"])

    assert window.status_text.text() == "Microphone blocked. Open Settings."
    assert window.status_text.accessibleDescription() == window.status_text.text()
    assert len(events) == 1
    assert events[0].message() == window.status_text.text()
    assert events[0].politeness() is QAccessible.AnnouncementPoliteness.Assertive
    window.close()


@pytest.mark.parametrize("scale", ["1", "1.25", "1.5", "2"])
def test_accessibility_probe_at_supported_scale(scale, tmp_path):
    temp_dir = tmp_path / f"scale-{scale.replace('.', '-') }"
    temp_dir.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "QT_QPA_PLATFORM": "offscreen",
            "QT_SCALE_FACTOR": scale,
            "ECHOLINK_DISABLE_HOTKEY": "1",
            "ECHOLINK_DISABLE_TRAY": "1",
            "ECHOLINK_DISABLE_SOUNDS": "1",
            "ECHOLINK_DISABLE_MEDIA_CONTROL": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TEMP": str(temp_dir),
            "TMP": str(temp_dir),
        }
    )
    completed = subprocess.run(
        [sys.executable, "-B", str(ROOT / "tools" / "accessibility_probe.py")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["ok"] is True
    assert payload["tabs_checked"] == 9
    assert payload["section_names"] == [
        "Dictate",
        "Writing tools",
        "Drafts",
        "Documents",
        "Vocabulary",
        "Snippets",
        "Recovery",
        "History",
        "Sources",
    ]
    assert payload["viewports_checked"] == 3
    assert payload["document_views_checked"] == 4
    assert float(payload["device_pixel_ratio"]) == pytest.approx(float(scale))
