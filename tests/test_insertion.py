from __future__ import annotations

import ctypes
import hashlib

import pytest

from echo_link_core import insertion
from echo_link_core.insertion import _Input
from echo_link_core.insertion import SelectionRecord, WindowSnapshot, WindowsTextInserter


def test_windows_input_structure_has_native_union_size():
    expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
    assert ctypes.sizeof(_Input) == expected


def test_clipboard_restore_does_not_overwrite_newer_content(monkeypatch):
    clipboard = {"value": "new dictation"}
    monkeypatch.setattr(insertion.pyperclip, "paste", lambda: clipboard["value"])
    monkeypatch.setattr(insertion.pyperclip, "copy", lambda value: clipboard.update(value=value))
    insertion._restore_clipboard("original", "old dictation")
    assert clipboard["value"] == "new dictation"
    insertion._restore_clipboard("original", "new dictation")
    assert clipboard["value"] == "original"


def test_clipboard_restore_retries_transient_read_and_write_locks(monkeypatch):
    clipboard = {"value": "dictation"}
    reads = 0
    writes = 0

    def paste():
        nonlocal reads
        reads += 1
        if reads == 1:
            raise RuntimeError("clipboard read locked")
        return clipboard["value"]

    def copy(value):
        nonlocal writes
        writes += 1
        if writes == 1:
            raise RuntimeError("clipboard write locked")
        clipboard["value"] = value

    monkeypatch.setattr(insertion.pyperclip, "paste", paste)
    monkeypatch.setattr(insertion.pyperclip, "copy", copy)
    monkeypatch.setattr(insertion.time, "sleep", lambda _seconds: None)

    assert insertion._restore_clipboard_with_retry("original", "dictation") is True
    assert clipboard["value"] == "original"
    assert reads == 3
    assert writes == 2


def test_clipboard_restore_retry_stops_on_newer_content(monkeypatch):
    reads = 0
    sleeps = []

    def paste():
        nonlocal reads
        reads += 1
        if reads == 1:
            raise RuntimeError("clipboard read locked")
        return "new user clipboard content"

    monkeypatch.setattr(insertion.pyperclip, "paste", paste)
    monkeypatch.setattr(
        insertion.pyperclip,
        "copy",
        lambda _value: pytest.fail("newer clipboard content must not be overwritten"),
    )
    monkeypatch.setattr(
        insertion.time,
        "sleep",
        lambda seconds: sleeps.append(seconds),
    )

    assert insertion._restore_clipboard_with_retry("original", "dictation") is False
    assert reads == 2
    assert sleeps == [insertion._CLIPBOARD_RESTORE_RETRY_SECONDS]


def test_clipboard_restore_schedule_uses_daemon_worker(monkeypatch):
    created = []

    class FakeTimer:
        def __init__(self, interval, function, args):
            self.interval = interval
            self.function = function
            self.args = args
            self.daemon = False
            self.started = False
            created.append(self)

        def start(self):
            self.started = True

    monkeypatch.setattr(insertion.threading, "Timer", FakeTimer)

    insertion._schedule_clipboard_restore("original", "dictation")

    assert len(created) == 1
    timer = created[0]
    assert timer.interval == insertion._CLIPBOARD_RESTORE_DELAY_SECONDS
    assert timer.function is insertion._restore_clipboard_with_retry
    assert timer.args == ("original", "dictation")
    assert timer.daemon is True
    assert timer.started is True


def test_replace_selection_refuses_changed_text(monkeypatch):
    inserter = WindowsTextInserter()
    window = WindowSnapshot(10, 20, "Editor")
    original = SelectionRecord("original", window)
    monkeypatch.setattr(inserter, "_activate", lambda _window: True)
    monkeypatch.setattr(inserter, "capture_selection", lambda: SelectionRecord("changed", window))
    assert inserter.replace_selection(original, "replacement") is None


def test_replace_selection_uses_uia_provider_without_clipboard_capture(monkeypatch):
    inserter = WindowsTextInserter()
    window = WindowSnapshot(10, 20, "Editor")
    original = SelectionRecord("Original sentence.", window)
    scopes = []
    inserted = object()
    inserter.set_selection_provider(
        lambda scope: scopes.append(scope) or SelectionRecord("Original sentence.", window)
    )
    monkeypatch.setattr(inserter, "_activate", lambda _window: True)
    monkeypatch.setattr(
        inserter,
        "capture_selection",
        lambda: pytest.fail("UI Automation recheck must not use the clipboard"),
    )
    monkeypatch.setattr(
        inserter,
        "insert",
        lambda text, *, expected_window=None, _mode_override=None,
        _restore_clipboard_before_return=False: inserted,
    )

    assert inserter.replace_selection(original, "Corrected sentence.") is inserted
    assert scopes == ["selection"]


def test_word_document_replace_uses_direct_adapter_without_foreground_recheck(monkeypatch):
    inserter = WindowsTextInserter()
    window = WindowSnapshot(10, 20, "Document1 - Word", "OpusApp")
    original = SelectionRecord(
        "First bad sentence.\n\nSecond bad sentence.",
        window,
        "document",
        "a" * 64,
    )
    monkeypatch.setattr(insertion.sys, "platform", "win32")
    monkeypatch.setattr(insertion, "_replace_word_document_text", lambda *_args: True)
    monkeypatch.setattr(
        inserter,
        "_activate",
        lambda _window: pytest.fail("Word COM replacement must not depend on HUD focus"),
    )

    result = inserter.replace_selection(
        original,
        "First corrected sentence.\n\nSecond corrected sentence.",
    )

    assert result is not None
    assert result.text == "First corrected sentence.\n\nSecond corrected sentence."
    assert result.window == window
    assert inserter.last_record == result


def test_word_document_adapter_verifies_identity_and_preserves_paragraphs(monkeypatch):
    assigned = []
    activated = []

    class _Range:
        @property
        def Text(self):
            return assigned[-1] if assigned else ""

        @Text.setter
        def Text(self, value):
            assigned.append(value)

    class _Content:
        Text = "First bad sentence.\r\rSecond bad sentence.\r"
        End = len(Text)

    class _Document:
        Name = "Document1"
        Content = _Content()

        def Activate(self):
            activated.append("document")

        def Range(self, start, end):
            assert (start, end) == (0, len(self.Content.Text) - 1)
            return _Range()

    class _Word:
        ActiveDocument = _Document()

        def Activate(self):
            activated.append("word")

    monkeypatch.setattr(insertion, "_active_word_application", lambda: _Word())
    source = "First bad sentence.\n\nSecond bad sentence."
    record = SelectionRecord(
        source,
        WindowSnapshot(10, 20, "Document1 - Word", "OpusApp"),
        "selection",
        hashlib.sha256(source.encode("utf-8")).hexdigest(),
        (1, 2, 3),
        (10, 20, 500, 600),
        0,
        len(source),
    )

    assert insertion._replace_word_document_text(
        record,
        "First corrected sentence.\n\nSecond corrected sentence.",
    ) is True
    assert assigned == ["First corrected sentence.\r\rSecond corrected sentence."]
    assert activated == ["document", "word"]

def test_word_document_adapter_accepts_uia_text_without_trailing_blank_paragraph(
    monkeypatch,
):
    assigned = []

    class _Range:
        @property
        def Text(self):
            return assigned[-1] if assigned else ""

        @Text.setter
        def Text(self, value):
            assigned.append(value)

    class _Content:
        Text = "First bad sentence.\r\rSecond bad sentence.\r\r"
        End = len(Text)

    class _Document:
        Name = "Document1"
        Content = _Content()

        def Activate(self):
            pass

        def Range(self, start, end):
            assert (start, end) == (0, len(self.Content.Text) - 1)
            return _Range()

    class _Word:
        ActiveDocument = _Document()

        def Activate(self):
            pass

    monkeypatch.setattr(insertion, "_active_word_application", lambda: _Word())
    source = "First bad sentence.\n\nSecond bad sentence."
    record = SelectionRecord(
        source,
        WindowSnapshot(10, 20, "Document1 - Word", "OpusApp"),
        "document",
        hashlib.sha256(source.encode("utf-8")).hexdigest(),
    )

    assert insertion._replace_word_document_text(
        record,
        "First corrected sentence.\n\nSecond bad sentence.",
    ) is True
    assert assigned == ["First corrected sentence.\r\rSecond bad sentence."]
def test_word_range_adapter_preserves_embedded_object_anchor(monkeypatch):
    state = {
        "text": "First bad sentence.\r\x01\rSecond bad sentence.\r",
        "assigned": [],
    }

    class _Content:
        @property
        def Text(self):
            return state["text"]

        @property
        def End(self):
            return len(state["text"])

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
                state["text"][: self.start]
                + value
                + state["text"][self.end :]
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
    captured = "First bad sentence.\r\rSecond bad sentence."
    source = captured.replace("\r", "\n")
    start = source.index("Second bad sentence.")
    raw_start = state["text"].index("Second bad sentence.")
    record = SelectionRecord(
        captured,
        WindowSnapshot(10, 20, "Document1 - Word", "OpusApp"),
        "document",
        hashlib.sha256(source.encode("utf-8")).hexdigest(),
    )

    assert insertion._replace_word_document_range(
        record,
        expected_document_text=captured,
        start=start,
        end=len(source),
        expected_text="Second bad sentence.",
        new_text="Second corrected sentence.",
    ) is True
    assert state["assigned"] == [
        (
            raw_start,
            raw_start + len("Second bad sentence."),
            "Second corrected sentence.",
        )
    ]
    assert "\x01" in state["text"]

def test_replace_selection_recovers_dropped_uia_selection_before_insert(monkeypatch):
    inserter = WindowsTextInserter()
    window = WindowSnapshot(10, 20, "Editor")
    digest = "a" * 64
    original = SelectionRecord("Original sentence.", window, "selection", digest)
    provider_values = iter((None, original))
    restored = []
    inserted = object()
    inserter.set_selection_provider(lambda _scope: next(provider_values))
    inserter.set_selection_restorer(lambda record: restored.append(record) or True)
    monkeypatch.setattr(inserter, "_activate", lambda _window: True)
    monkeypatch.setattr(
        inserter,
        "capture_selection",
        lambda: pytest.fail("recovered UI Automation selection must not use clipboard capture"),
    )
    monkeypatch.setattr(
        inserter,
        "insert",
        lambda text, *, expected_window=None, _mode_override=None,
        _restore_clipboard_before_return=False: inserted,
    )

    assert inserter.replace_selection(original, "Rewritten sentence.") is inserted
    assert restored == [original]


def test_replace_selection_retries_transient_focus_recovery(monkeypatch):
    inserter = WindowsTextInserter()
    window = WindowSnapshot(10, 20, "Editor")
    original = SelectionRecord("Original sentence.", window, "selection", "a" * 64)
    provider_values = iter((None, original))
    restore_results = iter((False, False, True))
    restore_calls = []
    inserted = object()
    inserter.set_selection_provider(lambda _scope: next(provider_values))
    inserter.set_selection_restorer(
        lambda record: restore_calls.append(record) or next(restore_results)
    )
    monkeypatch.setattr(inserter, "_activate", lambda _window: True)
    monkeypatch.setattr(insertion.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        inserter,
        "capture_selection",
        lambda: pytest.fail("successful recovery must not use clipboard capture"),
    )
    monkeypatch.setattr(
        inserter,
        "insert",
        lambda text, *, expected_window=None, _mode_override=None,
        _restore_clipboard_before_return=False: inserted,
    )

    assert inserter.replace_selection(original, "Rewritten sentence.") is inserted
    assert restore_calls == [original, original, original]


def test_replace_document_rechecks_then_selects_all_and_inserts(monkeypatch):
    inserter = WindowsTextInserter()
    window = WindowSnapshot(10, 20, "Editor")
    original = SelectionRecord("First sentence. Second sentence.", window, "document")
    combos = []
    insert_calls = []
    inserted = object()
    inserter.set_selection_provider(lambda scope: SelectionRecord(original.text, window, scope))
    monkeypatch.setattr(inserter, "_activate", lambda _window: True)
    monkeypatch.setattr(insertion, "_send_combo", combos.append)
    monkeypatch.setattr(insertion, "_target_is_active", lambda _window: True)
    monkeypatch.setattr(insertion.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        inserter,
        "capture_selection",
        lambda: pytest.fail("document replacement must not use the clipboard for recheck"),
    )
    monkeypatch.setattr(
        inserter,
        "insert",
        lambda text, *, expected_window=None, _mode_override=None,
        _restore_clipboard_before_return=False: (
            insert_calls.append(
                (
                    text,
                    expected_window,
                    _mode_override,
                    _restore_clipboard_before_return,
                )
            )
            or inserted
        ),
    )

    assert inserter.replace_selection(original, "Rewritten document.") is inserted
    assert combos == [(insertion.VK_CONTROL, insertion.VK_A)]
    assert insert_calls == [
        (
            "Rewritten document.",
            window,
            insertion.InsertionMode.CLIPBOARD,
            True,
        )
    ]


def test_replace_document_recovers_exact_editor_focus_before_recheck(monkeypatch):
    inserter = WindowsTextInserter()
    window = WindowSnapshot(10, 20, "Editor")
    original = SelectionRecord(
        "First sentence. Second sentence.",
        window,
        "document",
        "a" * 64,
        (7, 8, 9),
    )
    provider_values = iter((None, original))
    restored = []
    inserted = object()
    inserter.set_selection_provider(lambda _scope: next(provider_values))
    inserter.set_selection_restorer(lambda record: restored.append(record) or True)
    monkeypatch.setattr(inserter, "_activate", lambda _window: True)
    monkeypatch.setattr(insertion, "_send_combo", lambda _keys: None)
    monkeypatch.setattr(insertion, "_target_is_active", lambda _window: True)
    monkeypatch.setattr(insertion.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        inserter,
        "insert",
        lambda text, *, expected_window=None, _mode_override=None,
        _restore_clipboard_before_return=False: inserted,
    )

    assert inserter.replace_selection(original, "Rewritten document.") is inserted
    assert restored == [original]


def test_replace_selection_forces_safe_clipboard_paste_even_in_direct_mode(monkeypatch):
    inserter = WindowsTextInserter(insertion.InsertionMode.DIRECT)
    window = WindowSnapshot(10, 20, "Editor")
    original = SelectionRecord("Bad sentnce.", window)
    calls = []
    inserted = object()
    inserter.set_selection_provider(lambda scope: SelectionRecord(original.text, window, scope))
    monkeypatch.setattr(inserter, "_activate", lambda _window: True)
    monkeypatch.setattr(
        inserter,
        "insert",
        lambda text, *, expected_window=None, _mode_override=None,
        _restore_clipboard_before_return=False: (
            calls.append(
                (
                    text,
                    expected_window,
                    _mode_override,
                    _restore_clipboard_before_return,
                )
            )
            or inserted
        ),
    )

    assert inserter.replace_selection(original, "Bad sentence.") is inserted
    assert calls == [
        (
            "Bad sentence.",
            window,
            insertion.InsertionMode.CLIPBOARD,
            True,
        )
    ]


def test_forced_replacement_paste_restores_clipboard_before_return(monkeypatch):
    target = WindowSnapshot(10, 20, "Editor")
    clipboard = {"value": "user clipboard"}
    events = []
    text = "First corrected sentence.\r\n\r\nSecond corrected paragraph."

    monkeypatch.setattr(insertion.sys, "platform", "win32")
    monkeypatch.setattr(insertion, "active_window", lambda: target)
    monkeypatch.setattr(insertion, "last_input_tick", lambda: 77)
    monkeypatch.setattr(insertion, "_release_stuck_modifiers", lambda: None)
    monkeypatch.setattr(insertion.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(insertion.pyperclip, "paste", lambda: clipboard["value"])
    monkeypatch.setattr(
        insertion,
        "copy_text_private",
        lambda value: clipboard.update(value=value),
    )
    monkeypatch.setattr(insertion, "_send_combo", lambda keys: events.append(("paste", keys)))
    monkeypatch.setattr(
        insertion,
        "_send_unicode_text",
        lambda *_args, **_kwargs: pytest.fail("replacement must use one clipboard paste"),
    )
    monkeypatch.setattr(
        insertion,
        "_schedule_clipboard_restore",
        lambda *_args: pytest.fail("replacement restore must finish before return"),
    )

    def restore(value, expected):
        events.append(("restore", value, expected))
        assert clipboard["value"] == expected
        clipboard["value"] = value
        return True

    monkeypatch.setattr(insertion, "_restore_clipboard_with_retry", restore)
    inserter = WindowsTextInserter(insertion.InsertionMode.DIRECT)

    record = inserter.insert(
        text,
        expected_window=target,
        _mode_override=insertion.InsertionMode.CLIPBOARD,
        _restore_clipboard_before_return=True,
    )

    assert record is not None
    assert events == [
        ("paste", (insertion.VK_CONTROL, insertion.VK_V)),
        ("restore", "user clipboard", text),
    ]
    assert clipboard["value"] == "user clipboard"
    assert inserter.mode is insertion.InsertionMode.DIRECT


def test_word_replacement_waits_for_async_paste_before_clipboard_restore(monkeypatch):
    target = WindowSnapshot(10, 20, "Document1 - Word", "OpusApp")
    clipboard = {"value": "user clipboard"}
    state = {"paste_pending": False, "pasted": None}
    text = "Corrected Word sentence."

    monkeypatch.setattr(insertion.sys, "platform", "win32")
    monkeypatch.setattr(insertion, "active_window", lambda: target)
    monkeypatch.setattr(insertion, "last_input_tick", lambda: 77)
    monkeypatch.setattr(insertion, "_release_stuck_modifiers", lambda: None)
    monkeypatch.setattr(insertion.pyperclip, "paste", lambda: clipboard["value"])
    monkeypatch.setattr(
        insertion,
        "copy_text_private",
        lambda value: clipboard.update(value=value),
    )
    monkeypatch.setattr(
        insertion,
        "_send_combo",
        lambda _keys: state.update(paste_pending=True),
    )

    def settle(seconds):
        if state["paste_pending"] and seconds >= 0.3:
            state["pasted"] = clipboard["value"]
            state["paste_pending"] = False

    monkeypatch.setattr(insertion.time, "sleep", settle)

    def restore(value, expected):
        clipboard["value"] = value
        return True

    monkeypatch.setattr(insertion, "_restore_clipboard_with_retry", restore)

    record = WindowsTextInserter().insert(
        text,
        expected_window=target,
        _mode_override=insertion.InsertionMode.CLIPBOARD,
        _restore_clipboard_before_return=True,
    )

    assert record is not None
    assert state["pasted"] == text
    assert clipboard["value"] == "user clipboard"


def test_capture_selection_aborts_before_clipboard_write_when_backup_fails(monkeypatch):
    target = WindowSnapshot(10, 20, "Editor")
    monkeypatch.setattr(insertion.sys, "platform", "win32")
    monkeypatch.setattr(insertion, "active_window", lambda: target)
    monkeypatch.setattr(
        insertion.pyperclip,
        "paste",
        lambda: (_ for _ in ()).throw(RuntimeError("clipboard locked")),
    )
    monkeypatch.setattr(
        insertion,
        "_stage_clipboard",
        lambda _text: pytest.fail("capture must not write when backup is unavailable"),
    )
    monkeypatch.setattr(
        insertion,
        "_send_combo",
        lambda _keys: pytest.fail("capture must not copy when backup is unavailable"),
    )

    with pytest.raises(RuntimeError, match="protect the Windows clipboard"):
        WindowsTextInserter().capture_selection()


def test_capture_selection_restores_exact_clipboard_before_return(monkeypatch):
    target = WindowSnapshot(10, 20, "Editor")
    clipboard = {"value": "original clipboard"}
    monkeypatch.setattr(insertion.sys, "platform", "win32")
    monkeypatch.setattr(insertion, "active_window", lambda: target)
    monkeypatch.setattr(insertion, "_release_stuck_modifiers", lambda: None)
    monkeypatch.setattr(insertion.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(insertion.pyperclip, "paste", lambda: clipboard["value"])
    monkeypatch.setattr(
        insertion,
        "_stage_clipboard",
        lambda text: clipboard.update(value=text) or True,
    )
    monkeypatch.setattr(
        insertion,
        "_send_combo",
        lambda _keys: clipboard.update(value="Selected words"),
    )
    monkeypatch.setattr(
        insertion,
        "_restore_clipboard_with_retry",
        lambda value, expected: (
            clipboard.update(value=value) or True
            if clipboard["value"] == expected
            else False
        ),
    )

    record = WindowsTextInserter().capture_selection()

    assert record == SelectionRecord("Selected words", target)
    assert clipboard["value"] == "original clipboard"


def test_insert_uses_direct_unicode_when_clipboard_backup_fails(monkeypatch):
    target = WindowSnapshot(10, 20, "Editor")
    sent = []

    monkeypatch.setattr(insertion.sys, "platform", "win32")
    monkeypatch.setattr(insertion, "active_window", lambda: target)
    monkeypatch.setattr(insertion, "last_input_tick", lambda: 77)
    monkeypatch.setattr(insertion, "_release_stuck_modifiers", lambda: None)
    monkeypatch.setattr(insertion.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        insertion.pyperclip,
        "paste",
        lambda: (_ for _ in ()).throw(RuntimeError("clipboard locked")),
    )
    monkeypatch.setattr(
        insertion.pyperclip,
        "copy",
        lambda _text: pytest.fail("fallback must not overwrite an unreadable clipboard"),
    )
    monkeypatch.setattr(
        insertion,
        "_send_combo",
        lambda _keys: pytest.fail("clipboard paste must not run"),
    )
    monkeypatch.setattr(
        insertion,
        "_send_unicode_text",
        lambda text, *, expected_window: sent.append((text, expected_window)) or True,
    )

    inserter = WindowsTextInserter()
    record = inserter.insert("Zażółć 😀")

    assert sent == [("Zażółć 😀", target)]
    assert record is not None
    assert record.text == "Zażółć 😀"
    assert record.window == target
    assert record.input_tick == 77


def test_direct_mode_never_reads_or_writes_clipboard(monkeypatch):
    target = WindowSnapshot(10, 20, "Editor")
    sent = []

    monkeypatch.setattr(insertion.sys, "platform", "win32")
    monkeypatch.setattr(insertion, "active_window", lambda: target)
    monkeypatch.setattr(insertion, "last_input_tick", lambda: 77)
    monkeypatch.setattr(insertion, "_release_stuck_modifiers", lambda: None)
    monkeypatch.setattr(insertion.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        insertion.pyperclip,
        "paste",
        lambda: pytest.fail("direct mode must not read the clipboard"),
    )
    monkeypatch.setattr(
        insertion.pyperclip,
        "copy",
        lambda _text: pytest.fail("direct mode must not write the clipboard"),
    )
    monkeypatch.setattr(
        insertion,
        "copy_text_private",
        lambda _text: pytest.fail("direct mode must not stage private clipboard text"),
    )
    monkeypatch.setattr(
        insertion,
        "_send_unicode_text",
        lambda text, *, expected_window: sent.append((text, expected_window)) or True,
    )

    inserter = WindowsTextInserter(insertion.InsertionMode.DIRECT)
    record = inserter.insert("Paste-blocked app")

    assert record is not None
    assert sent == [("Paste-blocked app", target)]


def test_clipboard_mode_reports_unavailable_clipboard_without_direct_retry(monkeypatch):
    target = WindowSnapshot(10, 20, "Editor")

    monkeypatch.setattr(insertion.sys, "platform", "win32")
    monkeypatch.setattr(insertion, "active_window", lambda: target)
    monkeypatch.setattr(insertion, "_release_stuck_modifiers", lambda: None)
    monkeypatch.setattr(insertion.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        insertion.pyperclip,
        "paste",
        lambda: (_ for _ in ()).throw(RuntimeError("clipboard locked")),
    )
    monkeypatch.setattr(
        insertion,
        "_send_unicode_text",
        lambda *_args, **_kwargs: pytest.fail("forced clipboard mode must not switch methods"),
    )

    inserter = WindowsTextInserter(insertion.InsertionMode.CLIPBOARD)
    with pytest.raises(RuntimeError, match="clipboard could not be read"):
        inserter.insert("Clipboard only")


def test_insert_uses_direct_unicode_when_clipboard_staging_fails(monkeypatch):
    target = WindowSnapshot(10, 20, "Editor")
    sent = []

    monkeypatch.setattr(insertion.sys, "platform", "win32")
    monkeypatch.setattr(insertion, "active_window", lambda: target)
    monkeypatch.setattr(insertion, "last_input_tick", lambda: 77)
    monkeypatch.setattr(insertion, "_release_stuck_modifiers", lambda: None)
    monkeypatch.setattr(insertion.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(insertion.pyperclip, "paste", lambda: "original")
    monkeypatch.setattr(
        insertion,
        "copy_text_private",
        lambda _text: (_ for _ in ()).throw(RuntimeError("clipboard write blocked")),
    )
    monkeypatch.setattr(
        insertion,
        "_send_combo",
        lambda _keys: pytest.fail("clipboard paste must not run"),
    )
    monkeypatch.setattr(
        insertion,
        "_send_unicode_text",
        lambda text, *, expected_window: sent.append((text, expected_window)) or True,
    )

    record = WindowsTextInserter().insert("Direct text")

    assert sent == [("Direct text", target)]
    assert record is not None


def test_insert_keeps_clipboard_paste_path_when_staging_succeeds(monkeypatch):
    target = WindowSnapshot(10, 20, "Editor")
    clipboard = {"value": "original"}
    pasted = []
    restores = []

    monkeypatch.setattr(insertion.sys, "platform", "win32")
    monkeypatch.setattr(insertion, "active_window", lambda: target)
    monkeypatch.setattr(insertion, "last_input_tick", lambda: 77)
    monkeypatch.setattr(insertion, "_release_stuck_modifiers", lambda: None)
    monkeypatch.setattr(insertion.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(insertion.pyperclip, "paste", lambda: clipboard["value"])
    monkeypatch.setattr(
        insertion,
        "copy_text_private",
        lambda text: clipboard.update(value=text),
    )
    monkeypatch.setattr(insertion, "_send_combo", lambda keys: pasted.append(keys))
    monkeypatch.setattr(
        insertion,
        "_send_unicode_text",
        lambda *_args, **_kwargs: pytest.fail("verified clipboard should use paste"),
    )
    monkeypatch.setattr(
        insertion,
        "_schedule_clipboard_restore",
        lambda value, expected: restores.append((value, expected)),
    )

    record = WindowsTextInserter().insert("Clipboard text")

    assert record is not None
    assert pasted == [(insertion.VK_CONTROL, insertion.VK_V)]
    assert restores == [("original", "Clipboard text")]


def test_direct_unicode_emits_utf16_key_down_and_up_events(monkeypatch):
    target = WindowSnapshot(10, 20, "Editor")
    batches = []
    monkeypatch.setattr(insertion, "_target_is_active", lambda _window: True)
    monkeypatch.setattr(
        insertion,
        "_send_inputs",
        lambda payloads, *, operation: batches.append((payloads, operation)),
    )

    monkeypatch.setattr(insertion, "_DIRECT_CHARACTER_DELAY_SECONDS", 0)
    assert insertion._send_unicode_text("A\u00e9", expected_window=target) is True

    assert len(batches) == 2
    assert all(operation == "direct Unicode input" for _, operation in batches)
    assert [[payload.union.ki.wScan for payload in payloads] for payloads, _ in batches] == [
        [0x0041, 0x0041],
        [0x00E9, 0x00E9],
    ]
    assert [payload.union.ki.dwFlags for payload in batches[-1][0]] == [
        insertion.KEYEVENTF_UNICODE,
        insertion.KEYEVENTF_UNICODE | insertion.KEYEVENTF_KEYUP,
    ]


def test_direct_unicode_rejects_supplementary_characters_before_input(monkeypatch):
    target = WindowSnapshot(10, 20, "Editor")
    monkeypatch.setattr(insertion, "_target_is_active", lambda _window: True)
    monkeypatch.setattr(
        insertion,
        "_send_inputs",
        lambda *_args, **_kwargs: pytest.fail("unsafe partial input must not start"),
    )

    with pytest.raises(RuntimeError, match="Use Auto or Clipboard"):
        insertion._send_unicode_text("A\U0001f600B", expected_window=target)


def test_direct_unicode_uses_safe_shift_enter_for_newlines(monkeypatch):
    target = WindowSnapshot(10, 20, "Editor")
    batches = []
    monkeypatch.setattr(insertion, "_target_is_active", lambda _window: True)
    monkeypatch.setattr(insertion, "_DIRECT_CHARACTER_DELAY_SECONDS", 0)
    monkeypatch.setattr(
        insertion,
        "_send_inputs",
        lambda payloads, *, operation: batches.append((payloads, operation)),
    )

    assert insertion._send_unicode_text("A\r\nB\nC", expected_window=target) is True

    assert len(batches) == 5
    newline_payloads = batches[1][0]
    assert [payload.union.ki.wVk for payload in newline_payloads] == [
        insertion.VK_SHIFT,
        insertion.VK_RETURN,
        insertion.VK_RETURN,
        insertion.VK_SHIFT,
    ]
    assert [payload.union.ki.dwFlags for payload in newline_payloads] == [
        0,
        0,
        insertion.KEYEVENTF_KEYUP,
        insertion.KEYEVENTF_KEYUP,
    ]


def test_direct_unicode_stops_if_focus_changes_between_batches(monkeypatch):
    target = WindowSnapshot(10, 20, "Editor")
    active = iter((True, False))
    batches = []
    monkeypatch.setattr(insertion, "_DIRECT_CHARACTER_DELAY_SECONDS", 0)
    monkeypatch.setattr(insertion, "_target_is_active", lambda _window: next(active))
    monkeypatch.setattr(
        insertion,
        "_send_inputs",
        lambda payloads, *, operation: batches.append((payloads, operation)),
    )

    with pytest.raises(insertion.PartialTextInsertionError, match="did not retry") as error:
        insertion._send_unicode_text("AB", expected_window=target)

    assert len(batches) == 1
    assert error.value.possibly_inserted_text == "A"
    assert error.value.remaining_text == "B"
    assert error.value.possibly_inserted_utf16_units == 1


def test_partial_sendinput_keeps_only_suffix_after_touched_character(monkeypatch):
    target = WindowSnapshot(10, 20, "Editor")
    calls = 0
    monkeypatch.setattr(insertion, "_target_is_active", lambda _window: True)
    monkeypatch.setattr(insertion, "_DIRECT_CHARACTER_DELAY_SECONDS", 0)

    def fail_on_second_character(payloads, *, operation):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise insertion.SendInputError(
                "partial",
                sent_events=1,
                requested_events=len(payloads),
            )

    monkeypatch.setattr(insertion, "_send_inputs", fail_on_second_character)

    with pytest.raises(insertion.PartialTextInsertionError) as error:
        insertion._send_unicode_text("ABC", expected_window=target)

    assert error.value.possibly_inserted_text == "AB"
    assert error.value.remaining_text == "C"
    assert error.value.possibly_inserted_utf16_units == 2
    assert error.value.total_utf16_units == 3


def test_sendinput_failure_surfaces_elevated_uipi_boundary(monkeypatch):
    class FailedSendInput:
        argtypes = None
        restype = None

        def __call__(self, *_args):
            return 0

    class FakeWinDll:
        user32 = type("User32", (), {"SendInput": FailedSendInput()})()
        kernel32 = type("Kernel32", (), {"GetLastError": lambda self: 0})()

    monkeypatch.setattr(insertion.ctypes, "windll", FakeWinDll())
    payload = insertion._keyboard_payload(key=insertion.VK_V)

    with pytest.raises(OSError, match="elevated-app UIPI boundary"):
        insertion._send_inputs([payload], operation="test input")


def test_insert_refuses_focus_change_before_any_input(monkeypatch):
    target = WindowSnapshot(10, 20, "Editor")
    other = WindowSnapshot(30, 40, "Other")
    windows = iter((target, other))

    monkeypatch.setattr(insertion.sys, "platform", "win32")
    monkeypatch.setattr(insertion, "active_window", lambda: next(windows))
    monkeypatch.setattr(insertion, "_release_stuck_modifiers", lambda: None)
    monkeypatch.setattr(insertion.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(insertion.pyperclip, "paste", lambda: "original")
    monkeypatch.setattr(
        insertion.pyperclip,
        "copy",
        lambda _text: pytest.fail("focus loss must stop before clipboard staging"),
    )
    monkeypatch.setattr(
        insertion,
        "_send_unicode_text",
        lambda *_args, **_kwargs: pytest.fail("focus loss must stop direct input"),
    )

    assert WindowsTextInserter().insert("Do not send", expected_window=target) is None


def test_insert_into_keeps_captured_target_for_final_insert(monkeypatch):
    target = WindowSnapshot(10, 20, "Editor")
    calls = []
    inserter = WindowsTextInserter()

    monkeypatch.setattr(insertion.sys, "platform", "win32")
    monkeypatch.setattr(insertion, "active_window", lambda: target)
    monkeypatch.setattr(insertion.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        inserter,
        "insert",
        lambda text, *, expected_window=None: calls.append((text, expected_window)) or object(),
    )

    assert inserter.insert_into(target, "Captured target") is not None
    assert calls == [("Captured target", target)]


def test_insert_into_waits_for_target_when_foreground_request_reports_failure(monkeypatch):
    target = WindowSnapshot(10, 20, "Editor")
    other = WindowSnapshot(30, 40, "Other")
    windows = iter((other, target))
    calls = []

    class FakeUser32:
        def IsWindow(self, handle):
            return handle == target.handle

        def GetWindowThreadProcessId(self, _handle, process_id):
            process_id._obj.value = target.process_id

        def ShowWindow(self, handle, command):
            calls.append(("show", handle, command))

        def SetForegroundWindow(self, handle):
            calls.append(("foreground", handle))
            return 0

    fake_windll = type("FakeWinDll", (), {"user32": FakeUser32()})()
    inserter = WindowsTextInserter()
    monkeypatch.setattr(insertion.sys, "platform", "win32")
    monkeypatch.setattr(insertion, "active_window", lambda: next(windows))
    monkeypatch.setattr(insertion.ctypes, "windll", fake_windll, raising=False)
    monkeypatch.setattr(insertion.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        inserter,
        "insert",
        lambda text, *, expected_window=None: calls.append((text, expected_window)) or object(),
    )

    assert inserter.insert_into(target, "Recovered focus") is not None
    assert ("foreground", target.handle) in calls
    assert ("Recovered focus", target) in calls


def test_activate_does_not_restore_or_resize_non_minimized_window(monkeypatch):
    target = WindowSnapshot(10, 20, "Maximized editor")
    other = WindowSnapshot(30, 40, "HUD")
    windows = iter((other, target))
    calls = []

    class FakeUser32:
        def IsWindow(self, _handle):
            return True

        def IsIconic(self, _handle):
            return False

        def GetWindowThreadProcessId(self, _handle, process_id):
            process_id._obj.value = target.process_id

        def ShowWindow(self, handle, command):
            calls.append(("show", handle, command))

        def SetForegroundWindow(self, handle):
            calls.append(("foreground", handle))

    monkeypatch.setattr(insertion.sys, "platform", "win32")
    monkeypatch.setattr(insertion, "active_window", lambda: next(windows))
    monkeypatch.setattr(
        insertion.ctypes,
        "windll",
        type("FakeWinDll", (), {"user32": FakeUser32()})(),
        raising=False,
    )

    assert WindowsTextInserter._activate(target) is True
    assert ("foreground", target.handle) in calls
    assert not any(call[0] == "show" for call in calls)


def test_activate_restores_window_only_when_minimized(monkeypatch):
    target = WindowSnapshot(10, 20, "Minimized editor")
    other = WindowSnapshot(30, 40, "HUD")
    windows = iter((other, target))
    calls = []

    class FakeUser32:
        def IsWindow(self, _handle):
            return True

        def IsIconic(self, _handle):
            return True

        def GetWindowThreadProcessId(self, _handle, process_id):
            process_id._obj.value = target.process_id

        def ShowWindow(self, handle, command):
            calls.append(("show", handle, command))

        def SetForegroundWindow(self, handle):
            calls.append(("foreground", handle))

    monkeypatch.setattr(insertion.sys, "platform", "win32")
    monkeypatch.setattr(insertion, "active_window", lambda: next(windows))
    monkeypatch.setattr(
        insertion.ctypes,
        "windll",
        type("FakeWinDll", (), {"user32": FakeUser32()})(),
        raising=False,
    )

    assert WindowsTextInserter._activate(target) is True
    assert ("show", target.handle, 9) in calls


def test_insert_into_stops_when_captured_target_cannot_be_activated(monkeypatch):
    target = WindowSnapshot(10, 20, "Editor")
    inserter = WindowsTextInserter()

    monkeypatch.setattr(insertion.sys, "platform", "win32")
    monkeypatch.setattr(inserter, "_activate", lambda window: False)
    monkeypatch.setattr(
        inserter,
        "insert",
        lambda *_args, **_kwargs: pytest.fail("text must not be sent to the wrong window"),
    )

    assert inserter.insert_into(target, "Do not send") is None


def test_insert_into_retries_transient_focus_loss_before_any_text_is_sent(monkeypatch):
    target = WindowSnapshot(10, 20, "Editor")
    calls = []
    inserter = WindowsTextInserter()

    monkeypatch.setattr(insertion.sys, "platform", "win32")
    monkeypatch.setattr(insertion.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        inserter,
        "_activate",
        lambda window: calls.append(("activate", window)) or True,
    )
    results = iter((None, object()))
    monkeypatch.setattr(
        inserter,
        "insert",
        lambda text, *, expected_window=None: (
            calls.append(("insert", text, expected_window)) or next(results)
        ),
    )

    assert inserter.insert_into(target, "Retry safely") is not None
    assert calls == [
        ("activate", target),
        ("insert", "Retry safely", target),
        ("activate", target),
        ("insert", "Retry safely", target),
    ]


def test_word_title_matches_a_document_whose_extension_windows_hides():
    """Replace Text did nothing in Word, silently, on a default Windows setup.

    ``Document.Name`` always carries the extension; the title bar only shows
    one when Explorer is set to show extensions for known file types, which is
    off by default. The substring test therefore compared "essay.doc" against
    "essay  -  Compatibility Mode - Word" and refused every replacement.
    """
    assert insertion._word_title_names_document(
        "01_Dogs_in_Bakersfield_USA  -  Compatibility Mode - Word",
        "01_Dogs_in_Bakersfield_USA.doc",
    )
    assert insertion._word_title_names_document(
        "Quarterly Report - Word", "Quarterly Report.docx"
    )
    # Still matches when the title does show the extension.
    assert insertion._word_title_names_document(
        "Quarterly Report.docx - Word", "Quarterly Report.docx"
    )
    # An unsaved document has no extension to strip.
    assert insertion._word_title_names_document("Document1 - Word", "Document1")


def test_word_title_still_refuses_a_different_document():
    assert not insertion._word_title_names_document(
        "Budget 2027 - Word", "Quarterly Report.docx"
    )
    assert not insertion._word_title_names_document("", "Quarterly Report.docx")
    # A bare extension must not match every Word window.
    assert not insertion._word_title_names_document("Budget 2027 - Word", ".docx")


class _FakeUser32:
    """Stands in for user32 so the foreground fallback can be tested off-Windows."""

    def __init__(self, *, succeed_first: bool, foreground: int = 999):
        self.succeed_first = succeed_first
        self.foreground = foreground
        self.set_calls = 0
        self.attach_calls: list[tuple[int, int, bool]] = []
        self.brought_to_top = 0

    def SetForegroundWindow(self, handle):  # noqa: N802 - mirrors the Win32 name
        self.set_calls += 1
        if self.set_calls == 1:
            return 1 if self.succeed_first else 0
        return 1

    def GetForegroundWindow(self):  # noqa: N802
        return self.foreground

    def GetWindowThreadProcessId(self, handle, _process_id):  # noqa: N802
        # Distinct thread ids for the foreground window and the target window.
        return 11 if handle == self.foreground else 22

    def AttachThreadInput(self, source, target, attach):  # noqa: N802
        self.attach_calls.append((source, target, bool(attach)))
        return 1

    def BringWindowToTop(self, handle):  # noqa: N802
        self.brought_to_top += 1
        return 1


def test_foreground_is_taken_without_attaching_when_windows_allows_it():
    user32 = _FakeUser32(succeed_first=True)

    assert insertion._set_foreground_window(user32, 4242) is True
    assert user32.attach_calls == []
    assert user32.brought_to_top == 0


def test_foreground_falls_back_to_attaching_input_queues():
    """Accept in Word died here.

    The HUD is shown without activation, so Manuscript is a background process
    when Accept runs and Windows refuses the foreground change outright. The
    replacement aborted with "target window could not be activated" before it
    reached any COM guard, and the user was told the document was busy.
    """
    user32 = _FakeUser32(succeed_first=False)

    assert insertion._set_foreground_window(user32, 4242) is True
    assert user32.brought_to_top == 1
    attached = [call for call in user32.attach_calls if call[2]]
    detached = [call for call in user32.attach_calls if not call[2]]
    # Both the foreground thread and the target thread are attached...
    assert {call[1] for call in attached} == {11, 22}
    # ...and every attachment is released, or Manuscript's message loop would
    # stay tied to another application's input queue.
    assert {call[1] for call in attached} == {call[1] for call in detached}


def test_foreground_detaches_even_when_the_call_raises():
    user32 = _FakeUser32(succeed_first=False)

    def explode(handle):
        raise OSError("BringWindowToTop failed")

    user32.BringWindowToTop = explode

    with pytest.raises(OSError):
        insertion._set_foreground_window(user32, 4242)

    detached = [call for call in user32.attach_calls if not call[2]]
    assert {call[1] for call in detached} == {11, 22}


def test_protected_view_is_named_instead_of_asking_for_a_selection():
    """A read-only document needs Enable Editing, not "highlight text".

    A draft opened from a download, an email attachment or a network share
    opens in Word's Protected View, where nothing can be selected at all.
    Telling the writer to highlight text is advice they have already
    followed, and it sends them round the same loop.
    """
    from echo_link_core.insertion import _no_selection_message, WindowSnapshot

    protected = WindowSnapshot(
        1, 2, "01_Dogs_in_Bakersfield_USA  -  Protected View  -  Word"
    )
    message = _no_selection_message(protected)
    assert "Protected View" in message
    assert "Enable Editing" in message

    ordinary = WindowSnapshot(1, 2, "01_Dogs_in_Bakersfield_USA - Word")
    assert _no_selection_message(ordinary) == (
        "No selected text was found. Highlight text and try again."
    )


def _word_range_fake(monkeypatch, com_text):
    """A minimal Word document whose Content.Text is exactly com_text."""
    state = {"text": com_text, "assigned": []}

    class _Content:
        @property
        def Text(self):
            return state["text"]

        @property
        def End(self):
            return len(state["text"])

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
    return state


def _replace_second_sentence(state):
    captured, _spans = insertion._word_visible_text_and_spans(state["text"])
    start = captured.index("Second")
    record = SelectionRecord(
        captured,
        WindowSnapshot(10, 20, "Document1 - Word", "OpusApp"),
        "document",
        hashlib.sha256(captured.encode("utf-8")).hexdigest(),
    )
    return insertion._replace_word_document_range(
        record,
        expected_document_text=captured,
        start=start,
        end=len(captured),
        expected_text=captured[start:],
        new_text="Second corrected sentence.",
    )


def test_word_range_replacement_allows_a_non_breaking_space(monkeypatch):
    """A non-breaking space is text, not an embedded object.

    Essay 1 paragraph 3 contains one in "Bakersfield , United States". The
    refusal treated every character UI Automation omits as an object, so a
    correct correction reported "a range containing an object", was never
    written back to the document, and the review then stalled on that section
    and dropped to "0 of 0".
    """
    state = _word_range_fake(
        monkeypatch, "First bad sentence.\rSecond bad\xa0sentence.\r"
    )
    assert _replace_second_sentence(state) is True
    assert state["assigned"], "the corrected sentence was never written back"
    assert "Second corrected sentence." in state["text"]


def test_word_range_replacement_still_refuses_an_object_anchor(monkeypatch):
    """U+0001 is where a shape or picture sits, and must still be refused.

    Widening the rule above must not let the writer's artwork be overwritten.
    """
    state = _word_range_fake(
        monkeypatch, "First bad sentence.\rSecond bad\x01sentence.\r"
    )
    assert _replace_second_sentence(state) is False
    assert state["assigned"] == []


@pytest.mark.xfail(
    reason=(
        "Known defect: Word reports a non-breaking space through one API and "
        "not the other, in both directions. This case comes from essay 12 of "
        "the corpus, captured with U+00A0 at offset 68, which the COM read "
        "drops. The two texts then differ by one character, the replacement is "
        "refused as a changed document, and after two refusals the review "
        "freezes at '1 of 10'. It cost essays 7, 12 and 14 of the 2026-08-12 "
        "batch. "
        "Not fixed by normalising both sides: "
        "test_capture_and_replacement_agree_on_a_word_document_digest holds the "
        "opposite case, where COM carries a leading U+00A0 that accessibility "
        "never reports. Deleting the character shifts every later offset and "
        "keeping it breaks that test, so the fix is to normalise once where the "
        "capture is built, not in the two comparisons."
    ),
    strict=True,
)
def test_a_non_breaking_space_reads_the_same_through_com_and_accessibility():
    """The counter freeze, reduced to two lines of text."""
    sentence = "Pets in Bakersfield," + chr(0xA0) + "United States are common."
    raw = sentence + chr(13)

    visible, spans = insertion._word_visible_text_and_spans(raw)
    captured = insertion._canonical_word_text(
        sentence
    )

    assert visible == captured
    assert len(spans) == len(visible), "one span per visible character"
