from __future__ import annotations

import importlib
import hashlib
import logging
import os
import re
import sys
import threading
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from typing import Callable, ContextManager

from .insertion import SelectionRecord, WindowSnapshot, active_window


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DetectedSelection:
    record: SelectionRecord
    anchor: tuple[int, int, int, int] | None


class WindowsSelectionReader:
    """Read one visible UI Automation text selection without the clipboard."""

    def __init__(
        self,
        *,
        max_characters: int = 20_000,
        automation_loader: Callable[[], object] | None = None,
        window_provider: Callable[[], WindowSnapshot] = active_window,
        own_process_id: int | None = None,
        include_caret_sentence: bool = False,
        include_word_page: bool = False,
    ) -> None:
        if (
            isinstance(max_characters, bool)
            or not isinstance(max_characters, int)
            or not 1 <= max_characters <= 1_000_000
        ):
            raise ValueError("Selection size limit must be between 1 and 1,000,000 characters.")
        self.max_characters = max_characters
        self._automation_loader = automation_loader or _load_uiautomation
        self._injected_automation = automation_loader is not None
        self._window_provider = window_provider
        self._own_process_id = os.getpid() if own_process_id is None else int(own_process_id)
        self.include_caret_sentence = bool(include_caret_sentence)
        self.include_word_page = bool(include_word_page)
        self._automation_module: object | None = None
        self._automation_lock = threading.Lock()

    def read(self) -> DetectedSelection | None:
        """Return a safe visible selection, or None when UIA cannot prove one."""
        if sys.platform != "win32" and not self._injected_automation:
            return None
        try:
            automation = self._automation()
            window = self._window_provider()
            if (
                window.handle <= 0
                or window.process_id == self._own_process_id
            ):
                return None
            focused = automation.GetFocusedControl()
            for control in _candidate_text_controls(
                focused,
                automation,
                window,
                own_process_id=self._own_process_id,
            ):
                try:
                    diagnostic = (
                        os.environ.get("ECHOLINK_WORD_DIAGNOSTIC") == "1"
                        and " - word" in window.title.casefold()
                    )
                    if diagnostic:
                        logger.info(
                            "Word HUD candidate type=%s id=%s name=%s",
                            getattr(control, "ControlTypeName", ""),
                            getattr(control, "AutomationId", ""),
                            getattr(control, "Name", ""),
                        )
                    if bool(control.IsPassword) or bool(control.IsOffscreen):
                        if diagnostic:
                            logger.info("Word HUD candidate rejected password-or-offscreen")
                        continue
                    if not (
                        _is_editable_text_control(control, automation)
                        or _is_office_editor_control(control, window)
                    ):
                        if diagnostic:
                            logger.info("Word HUD candidate rejected not-editable")
                        continue
                    pattern = _text_pattern(control, automation)
                    if pattern is None:
                        if diagnostic:
                            logger.info("Word HUD candidate rejected no-text-pattern")
                        continue
                    ranges = tuple(pattern.GetSelection() or ())
                    if len(ranges) != 1:
                        if diagnostic:
                            logger.info(
                                "Word HUD candidate rejected selection-count=%d",
                                len(ranges),
                            )
                        continue
                    selected_range = ranges[0]
                    text = selected_range.GetText(self.max_characters + 1)
                    if not isinstance(text, str):
                        if diagnostic:
                            logger.info("Word HUD candidate rejected non-text")
                        continue
                    if not text.strip():
                        if not self.include_caret_sentence:
                            continue
                        selected_range = _sentence_range_at_caret(
                            selected_range,
                            automation,
                            self.max_characters,
                        )
                        if selected_range is None:
                            if diagnostic:
                                logger.info("Word HUD candidate rejected no-caret-sentence")
                            continue
                        text = selected_range.GetText(self.max_characters + 1)
                        if not isinstance(text, str) or not text.strip():
                            if diagnostic:
                                logger.info("Word HUD candidate rejected empty-caret-sentence")
                            continue
                    if self.include_word_page and " - word" in window.title.casefold():
                        try:
                            page_unit = getattr(
                                getattr(automation, "TextUnit", None),
                                "Page",
                                None,
                            )
                            if page_unit is not None:
                                page_range = selected_range.Clone()
                                page_range.ExpandToEnclosingUnit(page_unit)
                                page_text = page_range.GetText(self.max_characters + 1)
                                # Grow, never shrink. Widening a caret or a
                                # short selection to its page is how the badge
                                # offers a useful amount to review. Applying it
                                # to a larger selection throws text away:
                                # selecting a whole two-page essay came back as
                                # page one, and paragraphs 8 to 10 were never
                                # offered at all.
                                if (
                                    isinstance(page_text, str)
                                    and page_text.strip()
                                    and len(page_text) > len(text)
                                    and len(page_text) <= self.max_characters
                                ):
                                    selected_range = page_range
                                    text = page_text
                        except Exception:
                            if diagnostic:
                                logger.exception("Word page expansion failed")
                    if len(text) > self.max_characters:
                        continue
                    anchor = _selection_anchor(selected_range)
                    if anchor is None:
                        if diagnostic:
                            logger.info("Word HUD candidate rejected no-anchor")
                        continue
                    runtime_id = _control_runtime_id(control)
                    document_digest = ""
                    selection_start = None
                    selection_end = None
                    document_range = getattr(pattern, "DocumentRange", None)
                    if document_range is not None:
                        document_text = document_range.GetText(self.max_characters + 1)
                        if (
                            isinstance(document_text, str)
                            and len(document_text) <= self.max_characters
                        ):
                            document_digest = _text_digest(document_text)
                            selection_start, selection_end = _selection_offsets(
                                document_range,
                                selected_range,
                                automation,
                                text,
                                self.max_characters,
                            )
                    return DetectedSelection(
                        SelectionRecord(
                            text,
                            window,
                            "selection",
                            document_digest,
                            runtime_id,
                            anchor if runtime_id else None,
                            selection_start,
                            selection_end,
                        ),
                        anchor,
                    )
                except Exception:
                    if diagnostic:
                        logger.exception("Word HUD candidate failed")
                    continue
            return None
        except Exception:
            return None

    def read_selection(self) -> DetectedSelection | None:
        return self.read()

    def has_editable_focus(self) -> bool:
        """Return whether the foreground focus still belongs to an editable text box."""
        if sys.platform != "win32" and not self._injected_automation:
            return False
        try:
            automation = self._automation()
            window = self._window_provider()
            if window.handle <= 0 or window.process_id == self._own_process_id:
                return False
            focused = automation.GetFocusedControl()
            return any(
                not bool(control.IsPassword)
                and not bool(control.IsOffscreen)
                and (
                    _is_editable_text_control(control, automation)
                    or _is_office_editor_control(control, window)
                )
                for control in _candidate_text_controls(
                    focused,
                    automation,
                    window,
                    own_process_id=self._own_process_id,
                )
            )
        except Exception:
            return False

    def read_editable_focus(self) -> DetectedSelection | None:
        """Return the focused editable text box, including an empty one."""
        if sys.platform != "win32" and not self._injected_automation:
            return None
        try:
            automation = self._automation()
            window = self._window_provider()
            if window.handle <= 0 or window.process_id == self._own_process_id:
                return None
            focused = automation.GetFocusedControl()
            for control in _candidate_text_controls(
                focused,
                automation,
                window,
                own_process_id=self._own_process_id,
            ):
                try:
                    if (
                        bool(control.IsPassword)
                        or bool(control.IsOffscreen)
                        or not (
                            _is_editable_text_control(control, automation)
                            or _is_office_editor_control(control, window)
                        )
                    ):
                        continue
                    pattern = _text_pattern(control, automation)
                    document_range = (
                        getattr(pattern, "DocumentRange", None)
                        if pattern is not None
                        else None
                    )
                    text = ""
                    anchor = None
                    if document_range is not None:
                        value = document_range.GetText(self.max_characters + 1)
                        if not isinstance(value, str) or len(value) > self.max_characters:
                            continue
                        text = value
                        anchor = _selection_anchor(document_range)
                    anchor = anchor or _control_anchor(control)
                    if anchor is None:
                        continue
                    runtime_id = _control_runtime_id(control)
                    return DetectedSelection(
                        SelectionRecord(
                            text,
                            window,
                            "document",
                            _text_digest(text),
                            runtime_id,
                            anchor if runtime_id else None,
                        ),
                        anchor,
                    )
                except Exception:
                    continue
            return None
        except Exception:
            return None

    def read_document(self) -> DetectedSelection | None:
        """Read the full focused editable text range without using the clipboard."""
        if sys.platform != "win32" and not self._injected_automation:
            return None
        try:
            automation = self._automation()
            window = self._window_provider()
            if (
                window.handle <= 0
                or window.process_id == self._own_process_id
            ):
                return None
            focused = automation.GetFocusedControl()
            for control in _candidate_text_controls(
                focused,
                automation,
                window,
                own_process_id=self._own_process_id,
            ):
                try:
                    if bool(control.IsPassword) or bool(control.IsOffscreen):
                        continue
                    if not (
                        _is_editable_text_control(control, automation)
                        or _is_office_editor_control(control, window)
                    ):
                        continue
                    pattern = _text_pattern(control, automation)
                    document_range = (
                        getattr(pattern, "DocumentRange", None)
                        if pattern is not None
                        else None
                    )
                    if document_range is None:
                        continue
                    text = document_range.GetText(self.max_characters + 1)
                    if (
                        not isinstance(text, str)
                        or not text.strip()
                        or len(text) > self.max_characters
                    ):
                        continue
                    anchor = _selection_anchor(document_range) or _control_anchor(
                        control
                    )
                    runtime_id = _control_runtime_id(control)
                    return DetectedSelection(
                        SelectionRecord(
                            text,
                            window,
                            "document",
                            _text_digest(text),
                            runtime_id,
                            anchor if runtime_id else None,
                        ),
                        anchor,
                    )
                except Exception:
                    continue
            return None
        except Exception:
            return None

    def read_page(self) -> DetectedSelection | None:
        """Read the current Microsoft Word page without expanding to the document."""
        if sys.platform != "win32" and not self._injected_automation:
            return None
        try:
            automation = self._automation()
            window = self._window_provider()
            if (
                window.handle <= 0
                or window.process_id == self._own_process_id
                or " - word" not in window.title.casefold()
            ):
                return None
            focused = automation.GetFocusedControl()
            for control in _candidate_text_controls(
                focused,
                automation,
                window,
                own_process_id=self._own_process_id,
            ):
                try:
                    if (
                        bool(control.IsPassword)
                        or bool(control.IsOffscreen)
                        or not _is_office_editor_control(control, window)
                    ):
                        continue
                    pattern = _text_pattern(control, automation)
                    if pattern is None:
                        continue
                    ranges = tuple(pattern.GetSelection() or ())
                    if len(ranges) != 1:
                        continue
                    page_range = ranges[0].Clone()
                    page_unit = getattr(
                        getattr(automation, "TextUnit", None),
                        "Page",
                        None,
                    )
                    if page_unit is None:
                        continue
                    page_range.ExpandToEnclosingUnit(page_unit)
                    page_text = page_range.GetText(self.max_characters + 1)
                    if (
                        not isinstance(page_text, str)
                        or not page_text.strip()
                        or len(page_text) > self.max_characters
                    ):
                        continue
                    anchor = _selection_anchor(page_range)
                    if anchor is None:
                        continue
                    runtime_id = _control_runtime_id(control)
                    document_digest = ""
                    selection_start = None
                    selection_end = None
                    document_range = getattr(pattern, "DocumentRange", None)
                    if document_range is not None:
                        document_text = document_range.GetText(self.max_characters + 1)
                        if (
                            isinstance(document_text, str)
                            and len(document_text) <= self.max_characters
                        ):
                            document_digest = _text_digest(document_text)
                            selection_start, selection_end = _selection_offsets(
                                document_range,
                                page_range,
                                automation,
                                page_text,
                                self.max_characters,
                            )
                    return DetectedSelection(
                        SelectionRecord(
                            page_text,
                            window,
                            "selection",
                            document_digest,
                            runtime_id,
                            anchor if runtime_id else None,
                            selection_start,
                            selection_end,
                        ),
                        anchor,
                    )
                except Exception:
                    continue
            return None
        except Exception:
            return None

    def text_rectangles(
        self,
        record: SelectionRecord,
        start: int,
        end: int,
        *,
        scroll_into_view: bool = False,
    ) -> tuple[tuple[int, int, int, int], ...]:
        """Return screen rectangles for an exact range in the captured document."""
        if start < 0 or end <= start or end > len(record.text):
            return ()
        if sys.platform != "win32" and not self._injected_automation:
            return ()
        try:
            automation = self._automation()
            candidates = [automation.GetFocusedControl()]
            candidates.extend(_controls_at_anchor(automation, record.control_anchor))
            document_range = None
            document_text = ""
            base_start = 0
            for control in candidates:
                if control is None or int(getattr(control, "ProcessId", 0) or 0) != record.window.process_id:
                    continue
                pattern = _text_pattern(control, automation)
                candidate_range = (
                    getattr(pattern, "DocumentRange", None) if pattern is not None else None
                )
                if candidate_range is None:
                    continue
                candidate_text = candidate_range.GetText(self.max_characters + 1)
                candidate_base = _record_text_offset(record, control, candidate_text)
                if candidate_base is not None:
                    document_range = candidate_range
                    document_text = candidate_text
                    base_start = candidate_base
                    break
            if document_range is None:
                root_getter = getattr(automation, "ControlFromHandle", None)
                root = root_getter(record.window.handle) if callable(root_getter) else None
                matches: list[tuple[object, str, int]] = []
                for control in _descendant_controls(root, maximum=4096):
                    if (
                        int(getattr(control, "ProcessId", 0) or 0)
                        != record.window.process_id
                    ):
                        continue
                    if str(getattr(control, "ControlTypeName", "") or "") not in {
                        "EditControl",
                        "DocumentControl",
                    }:
                        continue
                    pattern = _text_pattern(control, automation)
                    candidate_range = (
                        getattr(pattern, "DocumentRange", None)
                        if pattern is not None
                        else None
                    )
                    if candidate_range is None:
                        continue
                    candidate_text = candidate_range.GetText(self.max_characters + 1)
                    candidate_base = _record_text_offset(record, control, candidate_text)
                    if candidate_base is not None:
                        matches.append((candidate_range, candidate_text, candidate_base))
                        if len(matches) > 1:
                            return ()
                if len(matches) == 1:
                    document_range, document_text, base_start = matches[0]
            if document_range is None:
                return ()
            endpoint = automation.TextPatternRangeEndpoint
            text_unit = automation.TextUnit
            target = document_range.Clone()
            absolute_start = base_start + start
            absolute_end = base_start + end
            target.MoveEndpointByUnit(
                endpoint.Start,
                text_unit.Character,
                absolute_start,
                waitTime=0,
            )
            target.MoveEndpointByUnit(
                endpoint.End,
                text_unit.Character,
                absolute_end - len(document_text),
                waitTime=0,
            )
            if scroll_into_view:
                scroll = getattr(target, "ScrollIntoView", None)
                if callable(scroll):
                    scroll(True)
            return _clip_rectangles(_range_rectangles(target), record.control_anchor)
        except Exception:
            return ()

    def is_record_control_focused(self, record: SelectionRecord) -> bool:
        """Return whether UI Automation focus is still in the captured editor."""
        if sys.platform != "win32" and not self._injected_automation:
            return False
        try:
            automation = self._automation()
            control = automation.GetFocusedControl()
            if (
                control is None
                or int(getattr(control, "ProcessId", 0) or 0) != record.window.process_id
                or bool(getattr(control, "IsPassword", False))
                or bool(getattr(control, "IsOffscreen", False))
            ):
                return False
            if record.control_runtime_id:
                return _control_runtime_id(control) == record.control_runtime_id
            pattern = _text_pattern(control, automation)
            document_range = (
                getattr(pattern, "DocumentRange", None) if pattern is not None else None
            )
            if document_range is None:
                return False
            text = document_range.GetText(self.max_characters + 1)
            if not isinstance(text, str) or len(text) > self.max_characters:
                return False
            if record.document_digest:
                return _text_digest(text) == record.document_digest
            return record.text == text or (
                record.scope == "selection" and text.count(record.text) == 1
            )
        except Exception:
            return False

    def restore_selection(self, record: SelectionRecord) -> bool:
        """Restore the exact captured range or document in its original editor."""
        if (
            record.scope not in {"selection", "document"}
            or not record.text.strip()
            or not (record.document_digest or record.control_runtime_id)
        ):
            return False
        if sys.platform != "win32" and not self._injected_automation:
            return False
        try:
            automation = self._automation()
            window = self._window_provider()
            if (
                window.handle != record.window.handle
                or window.process_id != record.window.process_id
                or window.process_id <= 0
                or window.process_id == self._own_process_id
            ):
                return False
            focused = automation.GetFocusedControl()
            match = self._matching_range(focused, automation, record, window.process_id)
            if match is not None:
                return _focus_and_select(focused, match)

            # The HUD temporarily owns focus. Recover the exact editor at the
            # saved selection location before walking a large Chromium tree.
            # Runtime IDs prevent a nearby read-only text node from becoming
            # the paste target.
            for control in _controls_at_anchor(automation, record.control_anchor):
                matching_range = self._matching_range(
                    control,
                    automation,
                    record,
                    window.process_id,
                )
                if matching_range is not None:
                    return _focus_and_select(control, matching_range)

            # Tool windows such as the HUD can cause browsers and rich editors
            # to restore focus to the page instead of the original text box.
            # Search only the captured foreground window and require one exact,
            # unchanged document match before restoring the selection.
            control_from_handle = getattr(automation, "ControlFromHandle", None)
            root = control_from_handle(window.handle) if callable(control_from_handle) else None
            matches: list[tuple[object, object]] = []
            for control in _descendant_controls(root):
                if str(getattr(control, "ControlTypeName", "")) not in {
                    "EditControl",
                    "DocumentControl",
                }:
                    continue
                matching_range = self._matching_range(
                    control,
                    automation,
                    record,
                    window.process_id,
                )
                if matching_range is not None:
                    matches.append((control, matching_range))
                    if len(matches) > 1:
                        return False
            if len(matches) != 1:
                return False
            control, matching_range = matches[0]
            return _focus_and_select(control, matching_range)
        except Exception:
            return False

    def _matching_range(self, control, automation, record, process_id: int):
        if control is None:
            return None
        if (
            int(getattr(control, "ProcessId", 0) or 0) != process_id
            or bool(getattr(control, "IsPassword", False))
            or bool(getattr(control, "IsOffscreen", False))
        ):
            return None
        pattern = _text_pattern(control, automation)
        document_range = getattr(pattern, "DocumentRange", None) if pattern is not None else None
        if document_range is None:
            return None
        document_text = document_range.GetText(self.max_characters + 1)
        if (
            not isinstance(document_text, str)
            or len(document_text) > self.max_characters
            or document_text.count(record.text) != 1
        ):
            return None
        same_document = bool(
            record.document_digest
            and _text_digest(document_text) == record.document_digest
        )
        same_control = bool(
            record.control_runtime_id
            and _control_runtime_id(control) == record.control_runtime_id
        )
        if not (same_document or same_control):
            return None
        selection_start = getattr(record, "selection_start", None)
        selection_end = getattr(record, "selection_end", None)
        if (
            isinstance(selection_start, int)
            and isinstance(selection_end, int)
            and 0 <= selection_start < selection_end <= len(document_text)
        ):
            try:
                endpoint = automation.TextPatternRangeEndpoint
                text_unit = automation.TextUnit
                matching_range = document_range.Clone()
                matching_range.MoveEndpointByUnit(
                    endpoint.Start,
                    text_unit.Character,
                    selection_start,
                    waitTime=0,
                )
                matching_range.MoveEndpointByUnit(
                    endpoint.End,
                    text_unit.Character,
                    selection_end - len(document_text),
                    waitTime=0,
                )
                if matching_range.GetText(len(record.text) + 1) == record.text:
                    return matching_range
            except Exception:
                pass
        if document_text.count(record.text) != 1:
            return None
        matching_range = document_range.FindText(record.text, False, False)
        if (
            matching_range is None
            or matching_range.GetText(len(record.text) + 1) != record.text
        ):
            return None
        return matching_range

    @contextmanager
    def automation_thread_context(self):
        """Initialize UI Automation when the reader runs on a monitor thread."""
        if sys.platform != "win32" and not self._injected_automation:
            yield
            return
        automation = self._automation()
        initializer = getattr(automation, "UIAutomationInitializerInThread", None)
        context: ContextManager = initializer() if callable(initializer) else nullcontext()
        with context:
            yield

    def _automation(self):
        if self._automation_module is None:
            with self._automation_lock:
                if self._automation_module is None:
                    self._automation_module = self._automation_loader()
        return self._automation_module


class WindowsSelectionMonitor:
    """Poll for stable selections and emit each distinct selection once."""

    def __init__(
        self,
        reader: WindowsSelectionReader,
        callback: Callable[[DetectedSelection], None],
        *,
        cleared_callback: Callable[[], None] | None = None,
        poll_interval_seconds: float = 0.08,
        stable_seconds: float = 0.24,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not callable(callback):
            raise TypeError("Selection callback must be callable.")
        if cleared_callback is not None and not callable(cleared_callback):
            raise TypeError("Selection cleared callback must be callable.")
        if not 0.01 <= float(poll_interval_seconds) <= 5.0:
            raise ValueError("Selection polling interval must be between 0.01 and 5 seconds.")
        if not 0.0 <= float(stable_seconds) <= 10.0:
            raise ValueError("Selection debounce must be between 0 and 10 seconds.")
        self.reader = reader
        self.callback = callback
        self.cleared_callback = cleared_callback
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.stable_seconds = float(stable_seconds)
        self._clock = clock
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._paused = False
        self._candidate_key: tuple | None = None
        self._candidate_since = 0.0
        self._emitted_key: tuple | None = None

    @property
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event = threading.Event()
            self._candidate_key = None
            self._candidate_since = 0.0
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name="echo-link-selection-monitor",
            )
            self._thread.start()

    def pause(self) -> None:
        with self._lock:
            self._paused = True
            self._candidate_key = None
            self._candidate_since = 0.0

    def resume(self) -> None:
        with self._lock:
            self._paused = False
            self._candidate_key = None
            self._candidate_since = 0.0

    def stop(self, timeout: float = 2.0) -> None:
        with self._lock:
            thread = self._thread
            self._stop_event.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0.0, float(timeout)))
        with self._lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None
            self._candidate_key = None
            self._candidate_since = 0.0

    def poll_once(self) -> None:
        with self._lock:
            if self._paused or self._stop_event.is_set():
                return
        try:
            detected = self.reader.read()
        except Exception:
            detected = None
        empty_editable_focus = False
        if detected is None:
            focused_reader = getattr(self.reader, "read_editable_focus", None)
            try:
                if callable(focused_reader):
                    detected = focused_reader()
            except Exception:
                detected = None
        if detected is not None and not detected.record.text:
            empty_editable_focus = True
            detected = None
        if (
            detected is None
            and not empty_editable_focus
            and self._emitted_key is not None
        ):
            still_editable = getattr(self.reader, "has_editable_focus", None)
            try:
                if callable(still_editable) and still_editable():
                    return
            except Exception:
                pass
        now = self._clock()
        callback_value: DetectedSelection | None = None
        selection_cleared = False
        with self._lock:
            if self._paused or self._stop_event.is_set():
                return
            if detected is None:
                selection_cleared = self._emitted_key is not None
                self._candidate_key = None
                self._candidate_since = 0.0
                self._emitted_key = None
            else:
                key = _selection_key(detected)
                if key == self._emitted_key:
                    self._candidate_key = None
                    self._candidate_since = 0.0
                    return
                if key != self._candidate_key:
                    self._candidate_key = key
                    self._candidate_since = now
                    if self.stable_seconds > 0:
                        return
                if now - self._candidate_since < self.stable_seconds:
                    return
                self._emitted_key = key
                self._candidate_key = None
                self._candidate_since = 0.0
                callback_value = detected
        if selection_cleared and self.cleared_callback is not None:
            logger.info("Writing HUD editable focus cleared")
            try:
                self.cleared_callback()
            except Exception:
                pass
        if callback_value is not None:
            logger.info(
                "Writing HUD editable focus detected title=%r scope=%s chars=%d anchor=%r",
                callback_value.record.window.title,
                callback_value.record.scope,
                len(callback_value.record.text),
                callback_value.anchor,
            )
            with self._lock:
                if self._paused or self._stop_event.is_set():
                    return
            try:
                self.callback(callback_value)
            except Exception:
                return

    def _run(self) -> None:
        context_factory = getattr(self.reader, "automation_thread_context", None)
        try:
            context = context_factory() if callable(context_factory) else nullcontext()
            with context:
                while not self._stop_event.is_set():
                    self.poll_once()
                    self._stop_event.wait(self.poll_interval_seconds)
        except Exception:
            logger.exception("Writing HUD selection monitor stopped unexpectedly")
        finally:
            with self._lock:
                if self._thread is threading.current_thread():
                    self._thread = None


def _load_uiautomation():
    return importlib.import_module("uiautomation")


# Characters Word's COM text contains that UI Automation never reports;
# kept in step with insertion._WORD_UIA_OMITTED_CHARACTERS.
_COM_ONLY_CHARACTERS = frozenset({chr(0x01), chr(0xA0)})


def _text_digest(text: str) -> str:
    """Digest a document the same way the replacement side reads it back.

    Word gives UI Automation a bare carriage return for each paragraph mark
    and gives COM a carriage return plus the characters COM alone reports.
    The replacement path normalises those before comparing, so digesting the
    raw accessibility text here meant the two never agreed on any document
    with more than one paragraph: Accept was refused every time with "Word
    COM range replacement refused changed document text", on a document the
    writer had not touched.
    """
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = "".join(
        character
        for character in value
        if character not in _COM_ONLY_CHARACTERS
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _selection_offsets(
    document_range,
    selected_range,
    automation,
    selected_text: str,
    max_characters: int,
) -> tuple[int | None, int | None]:
    """Capture stable document-relative endpoints before a tool window takes focus."""
    try:
        endpoint = automation.TextPatternRangeEndpoint
        prefix = document_range.Clone()
        moved = prefix.MoveEndpointByRange(
            endpoint.End,
            selected_range,
            endpoint.Start,
            waitTime=0,
        )
        if moved is False:
            return None, None
        prefix_text = prefix.GetText(max_characters + 1)
        if not isinstance(prefix_text, str):
            return None, None
        start = len(prefix_text)
        end = start + len(selected_text)
        document_text = document_range.GetText(max_characters + 1)
        if (
            not isinstance(document_text, str)
            or end > len(document_text)
            or document_text[start:end] != selected_text
        ):
            return None, None
        return start, end
    except Exception:
        return None, None


def _record_text_offset(record: SelectionRecord, control, document_text) -> int | None:
    if not isinstance(document_text, str):
        return None
    if record.scope == "document":
        return 0 if document_text == record.text else None
    same_document = bool(
        record.document_digest
        and _text_digest(document_text) == record.document_digest
    )
    same_control = bool(
        record.control_runtime_id
        and _control_runtime_id(control) == record.control_runtime_id
    )
    if not (same_document or same_control):
        return None
    start = getattr(record, "selection_start", None)
    end = getattr(record, "selection_end", None)
    if (
        isinstance(start, int)
        and isinstance(end, int)
        and 0 <= start < end <= len(document_text)
        and document_text[start:end] == record.text
    ):
        return start
    return document_text.find(record.text) if document_text.count(record.text) == 1 else None


def _sentence_range_at_caret(caret_range, automation, max_characters: int):
    try:
        text_unit = automation.TextUnit
        endpoint = automation.TextPatternRangeEndpoint
        paragraph_range = caret_range.Clone()
        paragraph_unit = getattr(text_unit, "Paragraph", text_unit.Line)
        if not paragraph_range.ExpandToEnclosingUnit(paragraph_unit, waitTime=0):
            return None
        paragraph_text = paragraph_range.GetText(max_characters + 1)
        if not isinstance(paragraph_text, str) or not paragraph_text.strip():
            return None
        prefix_range = paragraph_range.Clone()
        if not prefix_range.MoveEndpointByRange(
            endpoint.End,
            caret_range,
            endpoint.Start,
            waitTime=0,
        ):
            return None
        prefix = prefix_range.GetText(max_characters + 1)
        if not isinstance(prefix, str):
            return None
        bounds = _sentence_chunk_bounds_at_position(paragraph_text, len(prefix))
        if bounds is None:
            return None
        start, end = bounds
        sentence_range = paragraph_range.Clone()
        sentence_range.MoveEndpointByUnit(
            endpoint.Start,
            text_unit.Character,
            start,
            waitTime=0,
        )
        sentence_range.MoveEndpointByUnit(
            endpoint.End,
            text_unit.Character,
            end - len(paragraph_text),
            waitTime=0,
        )
        return sentence_range
    except Exception:
        return None


def _sentence_bounds_at_position(text: str, position: int) -> tuple[int, int] | None:
    spans = _sentence_spans(text)
    if not spans:
        return None
    point = max(0, min(len(text), int(position)))
    for span in spans:
        if span[0] <= point <= span[1]:
            return span
    prior = [span for span in spans if span[1] <= point]
    return prior[-1] if prior else spans[0]


def _sentence_chunk_bounds_at_position(
    text: str,
    position: int,
    *,
    maximum_sentences: int = 3,
) -> tuple[int, int] | None:
    spans = _sentence_spans(text)
    if not spans:
        return None
    point = max(0, min(len(text), int(position)))
    current_index = 0
    for index, span in enumerate(spans):
        if span[0] <= point <= span[1]:
            current_index = index
            break
        if span[1] <= point:
            current_index = index
    first_index = max(0, current_index - max(1, int(maximum_sentences)) + 1)
    return spans[first_index][0], spans[current_index][1]


def _sentence_spans(text: str) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    cursor = 0
    for match in re.finditer(r"[.!?]+[\"'\u201d\u2019)\]]*(?=\s|$)", text):
        left = cursor
        while left < match.end() and text[left].isspace():
            left += 1
        right = match.end()
        if right > left:
            spans.append((left, right))
        cursor = match.end()
    left = cursor
    while left < len(text) and text[left].isspace():
        left += 1
    right = len(text.rstrip())
    if right > left:
        spans.append((left, right))
    return tuple(spans)


def _text_pattern(control, automation):
    getter = getattr(control, "GetTextPattern", None)
    if callable(getter):
        return getter()
    generic_getter = getattr(control, "GetPattern", None)
    pattern_ids = getattr(automation, "PatternId", None)
    pattern_id = getattr(pattern_ids, "TextPattern", None)
    if callable(generic_getter) and pattern_id is not None:
        return generic_getter(pattern_id)
    return None


def _candidate_text_controls(
    focused,
    automation,
    window: WindowSnapshot,
    *,
    own_process_id: int,
):
    """Yield the focused editor, then Office's real document when focus is stale."""
    seen: set[tuple[int, ...] | int] = set()

    def add(control):
        if control is None:
            return None
        try:
            process_id = int(control.ProcessId)
        except (AttributeError, TypeError, ValueError):
            return None
        if (
            process_id <= 0
            or process_id == own_process_id
            or process_id != window.process_id
        ):
            return None
        runtime_id = _control_runtime_id(control)
        marker: tuple[int, ...] | int = runtime_id or id(control)
        if marker in seen:
            return None
        seen.add(marker)
        return control

    candidate = add(focused)
    if candidate is not None:
        yield candidate

    if not _is_office_editor_window(window):
        return
    root_getter = getattr(automation, "ControlFromHandle", None)
    if not callable(root_getter):
        return
    try:
        root = root_getter(window.handle)
    except Exception:
        return
    for control in _descendant_controls(root, maximum=4096):
        control_type = str(getattr(control, "ControlTypeName", "") or "")
        if not (
            (
                control_type == "DocumentControl"
                and _has_editable_word_body(control)
            )
            or (
                control_type == "EditControl"
                and str(getattr(control, "AutomationId", "") or "") == "Body"
            )
        ):
            continue
        candidate = add(control)
        if candidate is not None:
            yield candidate


def _is_editable_text_control(control, automation) -> bool:
    """Accept only enabled text-entry controls, never generic readable text."""
    if getattr(control, "IsEnabled", True) is False:
        return False
    if getattr(control, "IsKeyboardFocusable", True) is False:
        return False

    control_type = str(getattr(control, "ControlTypeName", "") or "")
    if control_type not in {"EditControl", "DocumentControl"}:
        return False

    explicit_read_only = getattr(control, "IsReadOnly", None)
    if explicit_read_only is True:
        return False
    if explicit_read_only is False:
        return True
    if control_type == "DocumentControl" and _has_editable_word_body(control):
        return True

    value_pattern = _optional_pattern(
        control,
        automation,
        getter_name="GetValuePattern",
        pattern_name="ValuePattern",
    )
    if value_pattern is not None:
        return getattr(value_pattern, "IsReadOnly", True) is False

    text_edit_pattern = _optional_pattern(
        control,
        automation,
        getter_name="GetTextEditPattern",
        pattern_name="TextEditPattern",
    )
    if text_edit_pattern is not None:
        return True

    # UIA identifies ordinary text boxes as EditControl even when their
    # provider does not expose ValuePattern. Generic documents fail closed.
    return control_type == "EditControl"


def _has_editable_word_body(control) -> bool:
    """Recognize Word's editable Document -> Page -> Body control shape."""
    for candidate in _descendant_controls(control, maximum=256):
        if candidate is control:
            continue
        if str(getattr(candidate, "ControlTypeName", "") or "") != "EditControl":
            continue
        if str(getattr(candidate, "AutomationId", "") or "") != "Body":
            continue
        if getattr(candidate, "IsEnabled", True) is False:
            continue
        if getattr(candidate, "IsKeyboardFocusable", True) is False:
            continue
        if getattr(candidate, "IsReadOnly", None) is True:
            continue
        return True
    return False


def _is_office_editor_control(control, window: WindowSnapshot) -> bool:
    """Accept an Office editor provider even when it hides its Body child."""
    return bool(
        _is_office_editor_window(window)
        and str(getattr(control, "ControlTypeName", "") or "") == "DocumentControl"
        and getattr(control, "IsEnabled", True) is not False
        and getattr(control, "IsKeyboardFocusable", True) is not False
        and getattr(control, "IsReadOnly", None) is not True
    )


def _is_office_editor_window(window: WindowSnapshot) -> bool:
    class_name = str(getattr(window, "class_name", "") or "").casefold()
    if class_name in {"opusapp", "_wwb", "rctrl_renwnd32"}:
        return True
    title = window.title.casefold()
    return bool(
        " - word" in title
        or " - message (" in title
        or title.endswith(" - appointment")
        or title.endswith(" - meeting")
    )


def _optional_pattern(control, automation, *, getter_name: str, pattern_name: str):
    getter = getattr(control, getter_name, None)
    if callable(getter):
        try:
            pattern = getter()
        except Exception:
            pattern = None
        if pattern is not None:
            return pattern
    generic_getter = getattr(control, "GetPattern", None)
    pattern_ids = getattr(automation, "PatternId", None)
    pattern_id = getattr(pattern_ids, pattern_name, None)
    if callable(generic_getter) and pattern_id is not None:
        try:
            return generic_getter(pattern_id)
        except Exception:
            return None
    return None


def _descendant_controls(root, *, maximum: int = 2048):
    if root is None:
        return
    pending = [root]
    seen: set[int] = set()
    while pending and len(seen) < maximum:
        control = pending.pop()
        marker = id(control)
        if marker in seen:
            continue
        seen.add(marker)
        yield control
        getter = getattr(control, "GetChildren", None)
        if not callable(getter):
            continue
        try:
            children = tuple(getter() or ())
        except Exception:
            continue
        pending.extend(reversed(children))


def _control_runtime_id(control) -> tuple[int, ...]:
    getter = getattr(control, "GetRuntimeId", None)
    if not callable(getter):
        return ()
    try:
        return tuple(int(value) for value in (getter() or ()))
    except (OSError, TypeError, ValueError):
        return ()


def _controls_at_anchor(automation, anchor: tuple[int, int, int, int] | None):
    if anchor is None:
        return
    getter = getattr(automation, "ControlFromPoint", None)
    if not callable(getter):
        return
    left, top, right, bottom = anchor
    points = (
        ((left + right) // 2, (top + bottom) // 2),
        (left + 2, top + 2),
        (right - 2, bottom - 2),
    )
    seen: set[tuple[int, ...] | int] = set()
    for x, y in points:
        try:
            control = getter(int(x), int(y))
        except Exception:
            continue
        for _depth in range(16):
            if control is None:
                break
            runtime_id = _control_runtime_id(control)
            marker: tuple[int, ...] | int = runtime_id or id(control)
            if marker not in seen:
                seen.add(marker)
                yield control
            parent_getter = getattr(control, "GetParentControl", None)
            if not callable(parent_getter):
                break
            try:
                control = parent_getter()
            except Exception:
                break


def _focus_and_select(control, matching_range) -> bool:
    focus = getattr(control, "SetFocus", None)
    if callable(focus):
        try:
            focus()
        except Exception:
            return False
    try:
        return bool(matching_range.Select())
    except Exception:
        return False


def _range_rectangles(selected_range) -> tuple[tuple[int, int, int, int], ...]:
    rectangles = selected_range.GetBoundingRectangles() or ()
    valid: list[tuple[int, int, int, int]] = []
    for rectangle in rectangles:
        try:
            left = int(rectangle.left)
            top = int(rectangle.top)
            right = int(rectangle.right)
            bottom = int(rectangle.bottom)
        except (AttributeError, TypeError, ValueError, OverflowError):
            continue
        if right > left and bottom > top:
            valid.append((left, top, right, bottom))
    return tuple(valid)


def _clip_rectangles(
    rectangles: tuple[tuple[int, int, int, int], ...],
    bounds: tuple[int, int, int, int] | None,
) -> tuple[tuple[int, int, int, int], ...]:
    if bounds is None:
        return rectangles
    bound_left, bound_top, bound_right, bound_bottom = bounds
    clipped: list[tuple[int, int, int, int]] = []
    for left, top, right, bottom in rectangles:
        item = (
            max(left, bound_left),
            max(top, bound_top),
            min(right, bound_right),
            min(bottom, bound_bottom),
        )
        if item[2] > item[0] and item[3] > item[1]:
            clipped.append(item)
    return tuple(clipped)


def _selection_anchor(selected_range) -> tuple[int, int, int, int] | None:
    # RichEdit raises COMError "Catastrophic failure" when asked for the
    # bounding rectangles of a large multi-line document range. The anchor
    # only places the badge, so it must degrade to None, never abort the
    # capture that carries the actual text.
    try:
        valid = _range_rectangles(selected_range)
    except Exception:
        return None
    if not valid:
        return None
    return (
        min(item[0] for item in valid),
        min(item[1] for item in valid),
        max(item[2] for item in valid),
        max(item[3] for item in valid),
    )


def _control_anchor(control) -> tuple[int, int, int, int] | None:
    rectangle = getattr(control, "BoundingRectangle", None)
    try:
        left = int(rectangle.left)
        top = int(rectangle.top)
        right = int(rectangle.right)
        bottom = int(rectangle.bottom)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _selection_key(detected: DetectedSelection) -> tuple:
    window = detected.record.window
    return (
        detected.record.text,
        window.handle,
        window.process_id,
        detected.anchor,
    )


__all__ = [
    "DetectedSelection",
    "WindowsSelectionMonitor",
    "WindowsSelectionReader",
]
