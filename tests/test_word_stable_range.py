from __future__ import annotations

import hashlib
import os
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

import echo_link_app.window as window_module
from echo_link_app.theme import apply_dark_theme
from echo_link_app.window import EchoLinkWindow
from echo_link_core import insertion
from echo_link_core.insertion import SelectionRecord, WindowSnapshot
from echo_link_core.storage import EchoLinkStore


@pytest.fixture(scope="module")
def app():
    os.environ["ECHOLINK_DISABLE_HOTKEY"] = "1"
    os.environ["ECHOLINK_DISABLE_SOUNDS"] = "1"
    os.environ["ECHOLINK_DISABLE_MEDIA_CONTROL"] = "1"
    application = QApplication.instance() or QApplication([])
    application.setStyle("Fusion")
    apply_dark_theme(application)
    return application


def test_word_selected_page_replaces_exact_absolute_range_and_refreshes_record(
    monkeypatch,
):
    state = {
        "text": "Earlier page.\rPage bad sentence.\rLater page.\r\x01",
        "assigned": [],
    }

    class _Content:
        Start = 0

        @property
        def Text(self):
            return state["text"]

    class _Range:
        def __init__(self, start, end):
            self.start = start
            self.end = end

        @property
        def Text(self):
            return state["text"][self.start : self.end]

        @Text.setter
        def Text(self, value):
            state["assigned"].append((self.start, self.end, value))
            state["text"] = (
                state["text"][: self.start] + value + state["text"][self.end :]
            )

    class _Document:
        Name = "Document1"
        Content = _Content()

        def Activate(self):
            pass

        def Range(self, start, end):
            return _Range(start, end)

    class _Word:
        ActiveDocument = _Document()

        def Activate(self):
            pass

    monkeypatch.setattr(insertion, "_active_word_application", lambda: _Word())
    whole_visible = "Earlier page.\nPage bad sentence.\nLater page."
    selected_page = "Page bad sentence."
    replacement = "Page corrected sentence."
    page_start = whole_visible.index(selected_page)
    record = SelectionRecord(
        selected_page,
        WindowSnapshot(10, 20, "Document1 - Word", "OpusApp"),
        "selection",
        hashlib.sha256(whole_visible.encode("utf-8")).hexdigest(),
        (1, 2, 3),
        (10, 20, 500, 600),
        page_start,
        page_start + len(selected_page),
    )

    updated = insertion._replace_word_document_range(
        record,
        expected_document_text=selected_page,
        start=0,
        end=len(selected_page),
        expected_text=selected_page,
        new_text=replacement,
    )

    assert isinstance(updated, SelectionRecord)
    assert updated.text == replacement
    assert updated.selection_start == page_start
    assert updated.selection_end == page_start + len(updated.text)
    assert state["assigned"] == [
        (
            page_start,
            page_start + len(selected_page),
            replacement,
        )
    ]
    assert state["text"] == (
        "Earlier page.\rPage corrected sentence.\rLater page.\r\x01"
    )


def test_word_page_accept_keeps_returned_stable_range_without_focus_read(
    app,
    tmp_path,
    monkeypatch,
):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "manuscript-v2.db"), enable_hotkey=False)
    source = "First bad sentence. Second bad sentence."
    page_start = 120
    record = SelectionRecord(
        source,
        WindowSnapshot(77, 88, "Document1 - Word", "OpusApp"),
        "selection",
        "a" * 64,
        (1, 2, 3),
        (10, 20, 500, 600),
        page_start,
        page_start + len(source),
    )
    corrected = "First corrected sentence. Second bad sentence."
    updated = replace(
        record,
        text=corrected,
        document_digest="b" * 64,
        selection_end=page_start + len(corrected),
    )
    window.selected_text_record = record
    monkeypatch.setattr(
        window.engine.inserter,
        "replace_document_range",
        lambda *_args, **_kwargs: updated,
    )

    class _Reader:
        def read_document(self):
            pytest.fail("Stable Word page replacement must not read HUD focus")

    monkeypatch.setattr(window_module, "WindowsSelectionReader", lambda **_kwargs: _Reader())
    suggestion = SimpleNamespace(
        start=0,
        end=len("First bad sentence."),
        original="First bad sentence.",
        replacement="First corrected sentence.",
    )

    try:
        assert window._replace_palette_step(corrected, suggestion) is True
        assert window.selected_text_record == updated
    finally:
        window._quitting = True
        window.close()
