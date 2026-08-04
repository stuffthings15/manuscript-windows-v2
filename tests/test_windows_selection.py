from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, replace
from types import SimpleNamespace

import pytest

import echo_link_core.windows_selection as selection_module
from echo_link_core.insertion import SelectionRecord, WindowSnapshot
from echo_link_core.windows_selection import (
    DetectedSelection,
    WindowsSelectionMonitor,
    WindowsSelectionReader,
)


@dataclass
class _Rect:
    left: int
    top: int
    right: int
    bottom: int


class _Range:
    def __init__(self, text="Selected text", rectangles=None):
        self.text = text
        self.rectangles = rectangles if rectangles is not None else [_Rect(10, 20, 70, 40)]
        self.selected = False
        self.last_found = None

    def GetText(self, max_length):
        return self.text[:max_length]

    def GetBoundingRectangles(self):
        return self.rectangles

    def FindText(self, text, backward, ignore_case):
        source = self.text.casefold() if ignore_case else self.text
        needle = text.casefold() if ignore_case else text
        if needle not in source:
            return None
        self.last_found = _Range(text, self.rectangles)
        return self.last_found

    def Select(self):
        self.selected = True
        return True


class _Pattern:
    def __init__(self, ranges, document_range=None):
        self.ranges = ranges
        self.DocumentRange = document_range

    def GetSelection(self):
        return self.ranges


@dataclass
class _ValuePattern:
    IsReadOnly: bool


class _Control:
    def __init__(
        self,
        ranges,
        *,
        document_range=None,
        process_id=222,
        password=False,
        offscreen=False,
        children=(),
        control_type="EditControl",
        runtime_id=(),
        parent=None,
        enabled=True,
        keyboard_focusable=True,
        read_only=None,
        value_pattern=None,
        text_edit_pattern=None,
        automation_id="",
        bounding_rectangle=None,
    ):
        self.ProcessId = process_id
        self.IsPassword = password
        self.IsOffscreen = offscreen
        self.IsEnabled = enabled
        self.IsKeyboardFocusable = keyboard_focusable
        if read_only is not None:
            self.IsReadOnly = read_only
        self.ControlTypeName = control_type
        self.AutomationId = automation_id
        self.BoundingRectangle = bounding_rectangle
        self.pattern = _Pattern(ranges, document_range)
        self.children = tuple(children)
        self.runtime_id = tuple(runtime_id)
        self.parent = parent
        self.focused = False
        self.value_pattern = value_pattern
        self.text_edit_pattern = text_edit_pattern

    def GetTextPattern(self):
        return self.pattern

    def GetValuePattern(self):
        return self.value_pattern

    def GetTextEditPattern(self):
        return self.text_edit_pattern

    def GetChildren(self):
        return self.children

    def SetFocus(self):
        self.focused = True

    def GetRuntimeId(self):
        return list(self.runtime_id)

    def GetParentControl(self):
        return self.parent


class _Automation:
    def __init__(self, control, root=None, point_control=None):
        self.control = control
        self.root = root
        self.point_control = point_control

    def GetFocusedControl(self):
        return self.control

    def ControlFromHandle(self, _handle):
        return self.root

    def ControlFromPoint(self, _x, _y):
        return self.point_control


def _reader(control, *, max_characters=100, loader=None, title="Editor"):
    window = WindowSnapshot(11, 222, title)
    return WindowsSelectionReader(
        max_characters=max_characters,
        automation_loader=loader or (lambda: _Automation(control)),
        window_provider=lambda: window,
        own_process_id=999,
    )


def test_reader_returns_selection_record_and_unioned_visible_anchor():
    selected_range = _Range(
        "Selected words",
        [_Rect(10, 20, 70, 40), _Rect(12, 40, 90, 60)],
    )
    detected = _reader(_Control([selected_range])).read()

    assert detected == DetectedSelection(
        SelectionRecord("Selected words", WindowSnapshot(11, 222, "Editor")),
        (10, 20, 90, 60),
    )


@pytest.mark.parametrize(
    "control",
    [
        _Control([_Range("Page heading")], control_type="TextControl"),
        _Control([_Range("Static article")], control_type="DocumentControl"),
        _Control([_Range("Disabled input")], enabled=False),
        _Control([_Range("Non-focusable input")], keyboard_focusable=False),
        _Control(
            [_Range("Read-only input")],
            value_pattern=_ValuePattern(IsReadOnly=True),
        ),
    ],
)
def test_reader_rejects_readable_text_that_is_not_an_editable_entry(control):
    assert _reader(control).read() is None


def test_reader_returns_empty_focused_editable_box_for_badge():
    control = _Control(
        [_Range("", rectangles=[])],
        document_range=_Range("", rectangles=[]),
        runtime_id=(4, 8),
        bounding_rectangle=_Rect(100, 200, 500, 260),
    )

    detected = _reader(control).read_editable_focus()

    assert detected is not None
    assert detected.record.text == ""
    assert detected.record.scope == "document"
    assert detected.record.control_runtime_id == (4, 8)
    assert detected.anchor == (100, 200, 500, 260)


def test_reader_accepts_focused_word_document_when_body_child_is_hidden():
    document_range = _Range("Word document text")
    control = _Control(
        [_Range("Word document text")],
        document_range=document_range,
        control_type="DocumentControl",
        runtime_id=(5, 9),
    )

    detected = _reader(control, title="Document1 - Word").read()

    assert detected is not None
    assert detected.record.text == "Word document text"


@pytest.mark.parametrize(
    "control",
    [
        _Control(
            [_Range("Editable document")],
            control_type="DocumentControl",
            read_only=False,
        ),
        _Control(
            [_Range("Rich editor")],
            control_type="DocumentControl",
            text_edit_pattern=object(),
        ),
        _Control(
            [_Range("Value editor")],
            control_type="DocumentControl",
            value_pattern=_ValuePattern(IsReadOnly=False),
        ),
    ],
)
def test_reader_accepts_documents_only_when_editability_is_proven(control):
    assert _reader(control).read() is not None


def test_reader_accepts_word_document_with_nested_editable_body():
    word_body = _Control(
        [],
        control_type="EditControl",
        automation_id="Body",
    )
    word_page = _Control(
        [],
        control_type="PageControl",
        children=(word_body,),
    )
    word_document = _Control(
        [_Range("Selected Word text")],
        control_type="DocumentControl",
        children=(word_page,),
    )

    assert _reader(word_document).read() is not None


def test_reader_falls_back_from_stale_word_search_focus_to_selected_document():
    word_body = _Control(
        [],
        control_type="EditControl",
        automation_id="Body",
    )
    word_page = _Control(
        [],
        control_type="PageControl",
        children=(word_body,),
    )
    word_document = _Control(
        [_Range("Selected Word text")],
        control_type="DocumentControl",
        children=(word_page,),
    )
    root = _Control(
        [],
        control_type="WindowControl",
        children=(word_document,),
    )
    stale_search = _Control([_Range("   ")])
    reader = WindowsSelectionReader(
        max_characters=100,
        automation_loader=lambda: _Automation(stale_search, root=root),
        window_provider=lambda: WindowSnapshot(11, 222, "Document1 - Word"),
        own_process_id=999,
    )

    detected = reader.read()

    assert detected is not None
    assert detected.record.text == "Selected Word text"


def test_reader_falls_back_from_stale_outlook_focus_to_word_editor_document():
    outlook_body = _Control(
        [],
        control_type="EditControl",
        automation_id="Body",
    )
    outlook_page = _Control(
        [],
        control_type="PageControl",
        children=(outlook_body,),
    )
    outlook_document = _Control(
        [_Range("Selected Outlook draft text")],
        control_type="DocumentControl",
        children=(outlook_page,),
    )
    root = _Control(
        [],
        control_type="WindowControl",
        children=(outlook_document,),
    )
    stale_ribbon = _Control([_Range("   ")])
    reader = WindowsSelectionReader(
        max_characters=100,
        automation_loader=lambda: _Automation(stale_ribbon, root=root),
        window_provider=lambda: WindowSnapshot(
            11,
            222,
            "Draft - Message (HTML)",
            "rctrl_renwnd32",
        ),
        own_process_id=999,
    )

    detected = reader.read()

    assert detected is not None
    assert detected.record.text == "Selected Outlook draft text"


def test_reader_does_not_search_document_tree_outside_word():
    editable_body = _Control(
        [_Range("Unfocused page text")],
        control_type="EditControl",
        automation_id="Body",
    )
    root = _Control(
        [],
        control_type="WindowControl",
        children=(editable_body,),
    )
    stale_search = _Control([_Range("   ")])
    reader = WindowsSelectionReader(
        max_characters=100,
        automation_loader=lambda: _Automation(stale_search, root=root),
        window_provider=lambda: WindowSnapshot(11, 222, "Web page"),
        own_process_id=999,
    )

    assert reader.read() is None


@pytest.mark.parametrize(
    "body",
    [
        _Control([], control_type="EditControl", automation_id="NotBody"),
        _Control([], control_type="EditControl", automation_id="Body", enabled=False),
        _Control(
            [],
            control_type="EditControl",
            automation_id="Body",
            read_only=True,
        ),
    ],
)
def test_reader_rejects_document_with_unusable_body_shape(body):
    document = _Control(
        [_Range("Static document text")],
        control_type="DocumentControl",
        children=(body,),
    )

    assert _reader(document).read() is None


def test_reader_returns_exact_target_text_rectangles():
    class TargetRange(_Range):
        def __init__(self):
            super().__init__("First sentence. Second sentence.", [_Rect(12, 40, 150, 60)])
            self.moves = []
            self.scrolls = []

        def Clone(self):
            return self

        def MoveEndpointByUnit(self, endpoint, unit, count, waitTime=0):
            self.moves.append((endpoint, unit, count, waitTime))
            return count

        def ScrollIntoView(self, align_to_top):
            self.scrolls.append(align_to_top)

    target = TargetRange()
    control = _Control([], document_range=target)
    automation = _Automation(control)
    automation.TextPatternRangeEndpoint = type("Endpoint", (), {"Start": 1, "End": 2})
    automation.TextUnit = type("TextUnit", (), {"Character": 3})
    reader = _reader(control, loader=lambda: automation)
    record = SelectionRecord(
        target.text,
        WindowSnapshot(11, 222, "Editor"),
        "document",
    )

    rectangles = reader.text_rectangles(record, 16, 32, scroll_into_view=True)

    assert rectangles == ((12, 40, 150, 60),)
    assert target.moves == [(1, 3, 16, 0), (2, 3, 0, 0)]
    assert target.scrolls == [True]


def test_reader_recovers_target_rectangles_from_source_window_after_hud_takes_focus():
    class TargetRange(_Range):
        def __init__(self):
            super().__init__("First sentence. Second sentence.", [_Rect(20, 50, 180, 72)])
            self.moves = []

        def Clone(self):
            return self

        def MoveEndpointByUnit(self, endpoint, unit, count, waitTime=0):
            self.moves.append((endpoint, unit, count, waitTime))
            return count

    target = TargetRange()
    editor = _Control(
        [],
        document_range=target,
        process_id=222,
        control_type="DocumentControl",
    )
    root = _Control([], process_id=222, children=(editor,), control_type="WindowControl")
    hud = _Control([], process_id=999)
    automation = _Automation(hud, root=root)
    automation.TextPatternRangeEndpoint = type("Endpoint", (), {"Start": 1, "End": 2})
    automation.TextUnit = type("TextUnit", (), {"Character": 3})
    reader = _reader(hud, loader=lambda: automation)
    record = SelectionRecord(
        target.text,
        WindowSnapshot(11, 222, "Editor"),
        "document",
    )

    assert reader.text_rectangles(record, 16, 32) == ((20, 50, 180, 72),)
    assert target.moves == [(1, 3, 16, 0), (2, 3, 0, 0)]


def test_target_rectangles_are_clipped_to_the_document_box():
    rectangles = ((10, 20, 900, 50), (20, 60, 200, 80))

    clipped = selection_module._clip_rectangles(rectangles, (15, 15, 300, 70))

    assert clipped == ((15, 20, 300, 50), (20, 60, 200, 70))


def test_reader_confirms_exact_captured_control_is_still_focused():
    document = _Range("Current document")
    control = _Control(
        [],
        document_range=document,
        runtime_id=(7, 8, 9),
    )
    record = SelectionRecord(
        document.text,
        WindowSnapshot(11, 222, "Editor"),
        "document",
        control_runtime_id=(7, 8, 9),
    )

    assert _reader(control).is_record_control_focused(record) is True

    other = _Control([], document_range=document, runtime_id=(1, 2, 3))
    assert _reader(other).is_record_control_focused(record) is False


def test_reader_returns_current_sentence_when_caret_is_collapsed(monkeypatch):
    caret = _Range("   ")
    sentence = _Range("Current sentence.", [_Rect(80, 40, 220, 60)])
    document_text = "Earlier sentence. Current sentence. Later sentence."
    monkeypatch.setattr(
        selection_module,
        "_sentence_range_at_caret",
        lambda current, automation, limit: sentence if current is caret else None,
    )
    reader = WindowsSelectionReader(
        max_characters=100,
        automation_loader=lambda: _Automation(
            _Control([caret], document_range=_Range(document_text))
        ),
        window_provider=lambda: WindowSnapshot(11, 222, "Editor"),
        own_process_id=999,
        include_caret_sentence=True,
    )

    detected = reader.read()

    assert detected == DetectedSelection(
        SelectionRecord(
            "Current sentence.",
            WindowSnapshot(11, 222, "Editor"),
            "selection",
            hashlib.sha256(document_text.encode("utf-8")).hexdigest(),
        ),
        (80, 40, 220, 60),
    )


def test_sentence_bounds_choose_sentence_containing_caret():
    text = "First sentence. Current sentence here. Last sentence."
    position = text.index("sentence here") + 4

    start, end = selection_module._sentence_bounds_at_position(text, position)

    assert text[start:end] == "Current sentence here."


def test_caret_review_chunk_includes_up_to_three_complete_sentences():
    text = "First sentence. Second sentence. Third sentence. Fourth sentence."

    start, end = selection_module._sentence_chunk_bounds_at_position(text, len(text))

    assert text[start:end] == "Second sentence. Third sentence. Fourth sentence."


def test_reader_returns_entire_document_with_document_scope():
    document = _Range(
        "First sentence. Second sentence.",
        [_Rect(10, 20, 300, 80)],
    )
    detected = _reader(_Control([], document_range=document)).read_document()

    assert detected == DetectedSelection(
        SelectionRecord(
            "First sentence. Second sentence.",
            WindowSnapshot(11, 222, "Editor"),
            "document",
            "219bc6effc75b730c9a1e6ecd0c18ad473138092421671aa4daf88352aee80e1",
        ),
        (10, 20, 300, 80),
    )


def test_reader_returns_word_document_when_body_child_is_temporarily_hidden():
    document = _Range(
        "First Word sentence. Second Word sentence.",
        [_Rect(10, 20, 300, 80)],
    )
    word_control = _Control(
        [_Range("First Word sentence.")],
        document_range=document,
        control_type="DocumentControl",
        children=(),
    )

    detected = _reader(word_control, title="Draft - Word").read_document()

    assert detected is not None
    assert detected.record.scope == "document"
    assert detected.record.text == document.text


def test_reader_fails_closed_for_missing_or_oversized_document():
    assert _reader(_Control([])).read_document() is None
    assert (
        _reader(
            _Control([], document_range=_Range("Document is too long")),
            max_characters=5,
        ).read_document()
        is None
    )


def test_reader_restores_unique_selection_only_when_document_is_unchanged():
    document_text = "First sentence. Selected sentence. Last sentence."
    document = _Range(document_text)
    reader = _reader(_Control([], document_range=document))
    record = SelectionRecord(
        "Selected sentence.",
        WindowSnapshot(11, 222, "Editor"),
        "selection",
        hashlib.sha256(document_text.encode("utf-8")).hexdigest(),
    )

    assert reader.restore_selection(record) is True
    assert document.last_found is not None
    assert document.last_found.selected is True

    duplicate = "Selected sentence. Selected sentence."
    duplicate_record = SelectionRecord(
        "Selected sentence.",
        record.window,
        "selection",
        hashlib.sha256(duplicate.encode("utf-8")).hexdigest(),
    )
    assert (
        _reader(_Control([], document_range=_Range(duplicate))).restore_selection(
            duplicate_record
        )
        is False
    )
    assert reader.restore_selection(replace(record, document_digest="0" * 64)) is False


def test_reader_restores_exact_document_control_after_hud_takes_focus():
    text = "First sentence. Second sentence."
    document = _Range(text)
    editor = _Control(
        [],
        document_range=document,
        runtime_id=(7, 8, 9),
    )
    root = _Control(
        [],
        process_id=222,
        children=(editor,),
        control_type="WindowControl",
    )
    hud = _Control([], process_id=999)
    automation = _Automation(hud, root=root)
    reader = _reader(hud, loader=lambda: automation)
    record = SelectionRecord(
        text,
        WindowSnapshot(11, 222, "Editor"),
        "document",
        hashlib.sha256(text.encode("utf-8")).hexdigest(),
        (7, 8, 9),
    )

    assert reader.restore_selection(record) is True
    assert editor.focused is True
    assert document.last_found is not None
    assert document.last_found.selected is True


def test_reader_records_and_restores_exact_word_range_by_offsets():
    class OffsetRange:
        def __init__(self, source, start, end, rectangles=None):
            self.source = source
            self.start = start
            self.end = end
            self.rectangles = rectangles or [_Rect(20, 40, 240, 64)]
            self.selected = False

        def GetText(self, maximum):
            return self.source[self.start : self.end][:maximum]

        def GetBoundingRectangles(self):
            return self.rectangles

        def Clone(self):
            return OffsetRange(
                self.source,
                self.start,
                self.end,
                self.rectangles,
            )

        def MoveEndpointByRange(
            self, endpoint, other, other_endpoint, waitTime=0
        ):
            value = other.start if other_endpoint == 1 else other.end
            if endpoint == 1:
                self.start = value
            else:
                self.end = value
            return True

        def MoveEndpointByUnit(self, endpoint, _unit, count, waitTime=0):
            if endpoint == 1:
                self.start += count
            else:
                self.end += count
            return count

        def FindText(self, *_args):
            return None

        def Select(self):
            self.selected = True
            return True

    source = "Before. Word range to replace. After."
    start = source.index("Word range")
    end = start + len("Word range to replace.")
    document = OffsetRange(source, 0, len(source))
    selected = OffsetRange(source, start, end)
    control = _Control(
        [selected],
        document_range=document,
        control_type="DocumentControl",
        runtime_id=(5, 9),
    )
    automation = _Automation(control)
    automation.TextPatternRangeEndpoint = type("Endpoint", (), {"Start": 1, "End": 2})
    automation.TextUnit = type("TextUnit", (), {"Character": 3})
    reader = _reader(
        control,
        loader=lambda: automation,
        title="Document1 - Word",
    )

    detected = reader.read()

    assert detected is not None
    assert getattr(detected.record, "selection_start", None) == start
    assert getattr(detected.record, "selection_end", None) == end
    assert reader.restore_selection(detected.record) is True


def test_reader_maps_selection_relative_underline_to_document_offsets():
    class OffsetRange:
        def __init__(self, source, start=0, end=None):
            self.source = source
            self.start = start
            self.end = len(source) if end is None else end
            self.moves = []

        def GetText(self, maximum):
            return self.source[self.start : self.end][:maximum]

        def GetBoundingRectangles(self):
            return [_Rect(40, 70, 220, 92)]

        def Clone(self):
            clone = OffsetRange(self.source, self.start, self.end)
            clone.moves = self.moves
            return clone

        def MoveEndpointByUnit(self, endpoint, _unit, count, waitTime=0):
            self.moves.append((endpoint, count))
            if endpoint == 1:
                self.start += count
            else:
                self.end += count
            return count

    source = "Before. Wrong words here. After."
    selection_start = source.index("Wrong")
    selected_text = "Wrong words here."
    document = OffsetRange(source)
    control = _Control(
        [],
        document_range=document,
        process_id=222,
        control_type="DocumentControl",
        runtime_id=(5, 9),
    )
    automation = _Automation(control)
    automation.TextPatternRangeEndpoint = type("Endpoint", (), {"Start": 1, "End": 2})
    automation.TextUnit = type("TextUnit", (), {"Character": 3})
    reader = _reader(control, loader=lambda: automation)
    record = SimpleNamespace(
        text=selected_text,
        window=WindowSnapshot(11, 222, "Document1 - Word"),
        scope="selection",
        document_digest=hashlib.sha256(source.encode("utf-8")).hexdigest(),
        control_runtime_id=(5, 9),
        control_anchor=None,
        selection_start=selection_start,
        selection_end=selection_start + len(selected_text),
    )

    rectangles = reader.text_rectangles(record, 6, 11)

    assert rectangles == ((40, 70, 220, 92),)
    assert document.moves == [
        (1, selection_start + 6),
        (2, selection_start + 11 - len(source)),
    ]


def test_reader_refocuses_unique_unchanged_text_box_inside_source_window():
    document_text = "First sentence. Selected sentence. Last sentence."
    target = _Control([], document_range=_Range(document_text))
    root = _Control([], children=(target,))
    page = _Control([], document_range=_Range("Browser page"))
    record = SelectionRecord(
        "Selected sentence.",
        WindowSnapshot(11, 222, "Browser"),
        "selection",
        hashlib.sha256(document_text.encode("utf-8")).hexdigest(),
    )
    reader = _reader(
        page,
        loader=lambda: _Automation(page, root),
    )

    assert reader.restore_selection(record) is True
    assert target.focused is True
    assert target.pattern.DocumentRange.last_found.selected is True


def test_reader_restores_exact_runtime_control_from_saved_selection_location():
    document = _Range("Selected sentence.")
    target = _Control(
        [],
        document_range=document,
        runtime_id=(42, 7),
    )
    page = _Control([], document_range=_Range("Browser page"), runtime_id=(9,))
    record = SelectionRecord(
        "Selected sentence.",
        WindowSnapshot(11, 222, "ChatGPT"),
        "selection",
        "",
        (42, 7),
        (100, 200, 300, 240),
    )
    reader = _reader(
        page,
        loader=lambda: _Automation(page, point_control=target),
    )

    assert reader.restore_selection(record) is True
    assert target.focused is True
    assert document.last_found is not None
    assert document.last_found.selected is True


@pytest.mark.parametrize(
    ("control", "max_characters"),
    [
        (_Control([]), 100),
        (_Control([_Range("one"), _Range("two")]), 100),
        (_Control([_Range("   ")]), 100),
        (_Control([_Range("secret")], password=True), 100),
        (_Control([_Range("hidden")], offscreen=True), 100),
        (_Control([_Range("own")], process_id=999), 100),
        (_Control([_Range("too long")]), 4),
        (_Control([_Range("no bounds", [])]), 100),
    ],
)
def test_reader_fails_closed_for_unsafe_or_ambiguous_selection(
    control, max_characters
):
    assert _reader(control, max_characters=max_characters).read() is None


def test_uiautomation_loading_is_lazy_and_reader_swallows_provider_errors():
    calls = []

    def loader():
        calls.append("loaded")
        raise RuntimeError("UIA unavailable")

    reader = _reader(_Control([_Range()]), loader=loader)
    assert calls == []
    assert reader.read() is None
    assert calls == ["loaded"]


def test_reader_rejects_focused_control_from_a_different_foreground_process():
    reader = WindowsSelectionReader(
        automation_loader=lambda: _Automation(_Control([_Range()], process_id=333)),
        window_provider=lambda: WindowSnapshot(11, 222, "Editor"),
        own_process_id=999,
    )

    assert reader.read() is None


class _Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class _SequenceReader:
    def __init__(self, values):
        self.values = list(values)
        self.last = None

    def read(self):
        if self.values:
            self.last = self.values.pop(0)
        return self.last


def _detected(text, left=10):
    return DetectedSelection(
        SelectionRecord(text, WindowSnapshot(11, 222, "Editor")),
        (left, 20, left + 60, 40),
    )


def test_monitor_debounces_emits_once_and_rearms_after_collapse_or_change():
    first = _detected("First")
    second = _detected("Second", 30)
    reader = _SequenceReader([first, first, first, first, None, first, first, second, second])
    clock = _Clock()
    emitted = []
    monitor = WindowsSelectionMonitor(
        reader,
        emitted.append,
        stable_seconds=0.2,
        clock=clock,
    )

    monitor.poll_once()
    clock.value = 0.1
    monitor.poll_once()
    assert emitted == []
    clock.value = 0.21
    monitor.poll_once()
    assert emitted == [first]
    clock.value = 1.0
    monitor.poll_once()
    assert emitted == [first]
    monitor.poll_once()  # collapsed selection rearms the monitor
    monitor.poll_once()
    clock.value = 1.21
    monitor.poll_once()
    assert emitted == [first, first]
    monitor.poll_once()
    clock.value = 1.42
    monitor.poll_once()
    assert emitted == [first, first, second]


def test_monitor_pause_resume_and_clean_thread_stop():
    detected = _detected("Stable")
    reader = _SequenceReader([detected])
    clock = _Clock()
    emitted = []
    monitor = WindowsSelectionMonitor(
        reader,
        emitted.append,
        poll_interval_seconds=0.01,
        stable_seconds=0,
        clock=clock,
    )
    monitor.pause()
    monitor.poll_once()
    assert emitted == []
    monitor.resume()
    monitor.poll_once()
    assert emitted == [detected]

    callback_seen = threading.Event()
    threaded = WindowsSelectionMonitor(
        _SequenceReader([_detected("Threaded")]),
        lambda _value: callback_seen.set(),
        poll_interval_seconds=0.01,
        stable_seconds=0,
    )
    threaded.start()
    assert callback_seen.wait(1.0)
    threaded.stop()
    assert threaded.running is False
    time.sleep(0.03)
    assert threaded.running is False


def test_monitor_reports_focus_loss_once_after_emitting_selection():
    detected = _detected("Stable")
    reader = _SequenceReader([detected, None, None])
    emitted = []
    cleared = []
    monitor = WindowsSelectionMonitor(
        reader,
        emitted.append,
        cleared_callback=lambda: cleared.append(True),
        stable_seconds=0,
    )

    monitor.poll_once()
    monitor.poll_once()
    monitor.poll_once()

    assert emitted == [detected]
    assert cleared == [True]


def test_monitor_does_not_report_focus_loss_before_a_selection_is_emitted():
    cleared = []
    monitor = WindowsSelectionMonitor(
        _SequenceReader([None, None]),
        lambda _detected: None,
        cleared_callback=lambda: cleared.append(True),
        stable_seconds=0,
    )

    monitor.poll_once()
    monitor.poll_once()

    assert cleared == []


def test_monitor_keeps_emitted_icon_while_editable_field_still_has_focus():
    detected = _detected("Stable")

    class _EditableReader(_SequenceReader):
        def has_editable_focus(self):
            return True

    cleared = []
    monitor = WindowsSelectionMonitor(
        _EditableReader([detected, None]),
        lambda _detected: None,
        cleared_callback=lambda: cleared.append(True),
        stable_seconds=0,
    )

    monitor.poll_once()
    monitor.poll_once()

    assert cleared == []


def test_monitor_waits_for_first_character_in_focused_text_box():
    empty = _detected("")
    typed = _detected("H")

    class _FocusReader:
        def __init__(self):
            self.focused = [empty, typed]

        def read(self):
            return None

        def read_editable_focus(self):
            return self.focused.pop(0)

    emitted = []
    monitor = WindowsSelectionMonitor(
        _FocusReader(),
        emitted.append,
        stable_seconds=0,
    )

    monitor.poll_once()
    assert emitted == []

    monitor.poll_once()
    assert emitted == [typed]


def test_monitor_clears_icon_when_last_character_is_deleted():
    typed = _detected("H")
    empty = _detected("")

    class _FocusReader:
        def __init__(self):
            self.focused = [typed, empty]

        def read(self):
            return None

        def read_editable_focus(self):
            return self.focused.pop(0)

        def has_editable_focus(self):
            return True

    emitted = []
    cleared = []
    monitor = WindowsSelectionMonitor(
        _FocusReader(),
        emitted.append,
        cleared_callback=lambda: cleared.append(True),
        stable_seconds=0,
    )

    monitor.poll_once()
    monitor.poll_once()

    assert emitted == [typed]
    assert cleared == [True]


def test_module_never_references_clipboard_fallback():
    from echo_link_core import windows_selection

    source = open(windows_selection.__file__, encoding="utf-8").read().casefold()
    assert "pyperclip" not in source
    assert "win32clipboard" not in source
    assert "qapplication.clipboard" not in source
