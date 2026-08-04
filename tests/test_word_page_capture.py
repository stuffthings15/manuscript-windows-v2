from __future__ import annotations

import hashlib
import os

import pytest
from PySide6.QtWidgets import QApplication

import echo_link_app.window as window_module
from echo_link_app.theme import apply_dark_theme
from echo_link_app.window import EchoLinkWindow
from echo_link_core.insertion import SelectionRecord, WindowSnapshot
from echo_link_core.storage import EchoLinkStore
from echo_link_core.windows_selection import DetectedSelection


@pytest.fixture(scope="module")
def app():
    os.environ["ECHOLINK_DISABLE_SOUNDS"] = "1"
    os.environ["ECHOLINK_DISABLE_MEDIA_CONTROL"] = "1"
    application = QApplication.instance() or QApplication([])
    application.setStyle("Fusion")
    apply_dark_theme(application)
    return application


def _word_page() -> SelectionRecord:
    text = (
        "First broken sentence. Second broken sentence. Third broken sentence. "
        "Fourth broken sentence. Fifth broken sentence.\r"
        "Sixth broken sentence. Seventh broken sentence."
    )
    return SelectionRecord(
        text,
        WindowSnapshot(77, 88, "Draft - Word"),
        "selection",
        hashlib.sha256(text.encode("utf-8")).hexdigest(),
        (1, 2, 3),
    )


def test_automatic_word_hud_uses_page_captured_by_background_monitor(
    app, tmp_path, monkeypatch
):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "manuscript-v2.db"), enable_hotkey=False)
    page = _word_page()
    detected = DetectedSelection(page, (100, 120, 500, 240))
    monkeypatch.setattr(
        window_module,
        "WindowsSelectionReader",
        lambda **_kwargs: pytest.fail("Word page capture ran on the Qt UI thread"),
    )

    window._handle_automatic_selection(detected)
    app.processEvents()

    assert window.selected_text_record == page
    assert window.rewrite_palette.source_edit.toPlainText().splitlines() == [
        (
            "First broken sentence. Second broken sentence. Third broken sentence. "
            "Fourth broken sentence. Fifth broken sentence."
        ),
        "Sixth broken sentence. Seventh broken sentence.",
    ]
    assert window.rewrite_palette._document_scope is False
    assert window.rewrite_palette._anchor == detected.anchor
    window.rewrite_palette.finish_choice()
    window._quitting = True
    window.close()


def test_selection_monitor_reader_is_configured_for_word_page_capture(
    app, tmp_path, monkeypatch
):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "manuscript-v2.db"), enable_hotkey=False)
    reader_kwargs: dict[str, object] = {}

    class _Reader:
        def __init__(self, **kwargs):
            reader_kwargs.update(kwargs)

    class _Monitor:
        def __init__(self, reader, callback, **kwargs):
            self.reader = reader
            self.callback = callback
            self.kwargs = kwargs
            self.running = False

        def start(self):
            self.running = True

        def stop(self, timeout=0):
            self.running = False

        def pause(self):
            pass

        def resume(self):
            pass

    monkeypatch.delenv("ECHOLINK_DISABLE_AUTO_HUD", raising=False)
    monkeypatch.setattr(window_module, "WindowsSelectionReader", _Reader)
    monkeypatch.setattr(window_module, "WindowsSelectionMonitor", _Monitor)
    window.enable_hotkey = True
    window.engine.settings.automatic_writing_hud = True

    window._start_selection_monitor()

    assert reader_kwargs["include_caret_sentence"] is True
    assert reader_kwargs["include_word_page"] is True
    window._quitting = True
    window.close()
