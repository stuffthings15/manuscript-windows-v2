from __future__ import annotations

import ctypes
import hashlib
import logging
import sys
import threading
import time
from dataclasses import dataclass, replace
from ctypes import wintypes
from typing import Callable

import pyperclip

from .models import InsertionMode
from .private_clipboard import copy_text_private


logger = logging.getLogger(__name__)

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_RETURN = 0x0D
VK_A = 0x41
VK_C = 0x43
VK_V = 0x56
VK_Z = 0x5A
MODIFIER_KEYS = (0x10, 0x11, 0x12, 0x5B, 0x5C)
_DIRECT_CHARACTER_DELAY_SECONDS = 0.001
_CLIPBOARD_PASTE_BASE_SETTLE_SECONDS = 0.04
_CLIPBOARD_PASTE_PER_CHARACTER_SECONDS = 0.0005
_CLIPBOARD_PASTE_MAX_SETTLE_SECONDS = 0.3
_OFFICE_REPLACEMENT_SETTLE_SECONDS = 0.5
_CLIPBOARD_RESTORE_DELAY_SECONDS = 0.2
_CLIPBOARD_RESTORE_RETRY_SECONDS = 0.05
_CLIPBOARD_RESTORE_TIMEOUT_SECONDS = 0.8
_TARGET_INSERT_ATTEMPTS = 3
_TARGET_INSERT_RETRY_SECONDS = 0.05
_SELECTION_RESTORE_ATTEMPTS = 3


def _normalize_newlines(text: str) -> str:
    """Map \r\n and bare \r to \n so editor conventions compare equal."""
    return text.replace("\r\n", "\n").replace("\r", "\n")
_SELECTION_RESTORE_RETRY_SECONDS = 0.06
_CLIPBOARD_UNAVAILABLE = object()
_WORD_COM_NOT_APPLICABLE = object()
# Characters Word's COM Content.Text contains that UI Automation never
# reports. U+0001 is the anchor Word puts where a shape or picture sits.
# U+00A0 is the non-breaking space: a document whose first character was one
# read as 5204 characters through COM and 5203 through UI Automation, so the
# two never matched, every offset after it was out by one, and Replace Text
# refused a document the writer had not touched.
_WORD_UIA_OMITTED_CHARACTERS = frozenset({chr(0x01)})
# A non-breaking space used to be deleted here too, on the assumption that
# accessibility never reports one. It does, and Word is not consistent about
# it: see windows_selection.normalize_captured_text. Deleting it moved every
# later offset, so a capture and a live read of one document could never be
# realigned. It is now the same plain space on both sides, which keeps the
# length and every span.
_WORD_NONBREAKING_SPACE = chr(0xA0)
# Only U+0001 actually stands for something that is not text. Refusing every
# range that merely contained a non-breaking space threw away correct
# corrections: essay 1 paragraph 3 has one in "Bakersfield , United States",
# so Replace Text reported "a range containing an object", the accept was
# never applied, and the review then stalled on that section and dropped to
# "0 of 0". The span builder above already skips both characters, so the
# offsets are right either way; this set decides only what is unsafe to
# overwrite.
_WORD_OBJECT_ANCHORS = frozenset({chr(0x01)})


class _LastInputInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


class _KeyboardInput(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _HardwareInput(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [
        ("mi", _MouseInput),
        ("ki", _KeyboardInput),
        ("hi", _HardwareInput),
    ]


class _Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", _InputUnion)]


class SendInputError(OSError):
    def __init__(self, message: str, *, sent_events: int, requested_events: int):
        super().__init__(message)
        self.sent_events = max(0, int(sent_events))
        self.requested_events = max(0, int(requested_events))


class PartialTextInsertionError(RuntimeError):
    def __init__(self, text: str, touched_utf16_units: int, *, reason: str):
        total_units = _utf16_unit_count(text)
        touched_units = max(0, min(total_units, int(touched_utf16_units)))
        prefix, remaining = _split_after_utf16_units(text, touched_units)
        super().__init__(
            f"{reason} Windows accepted input covering {touched_units} of {total_units} "
            "UTF-16 units. Manuscript did not retry. Check the target before using the "
            "unsent suffix."
        )
        self.possibly_inserted_text = prefix
        self.remaining_text = remaining
        self.possibly_inserted_utf16_units = touched_units
        self.total_utf16_units = total_units


@dataclass(frozen=True, slots=True)
class WindowSnapshot:
    handle: int
    process_id: int
    title: str
    class_name: str = ""


@dataclass(frozen=True, slots=True)
class InsertionRecord:
    text: str
    window: WindowSnapshot
    input_tick: int
    inserted_at: float


@dataclass(frozen=True, slots=True)
class SelectionRecord:
    text: str
    window: WindowSnapshot
    scope: str = "selection"
    document_digest: str = ""
    control_runtime_id: tuple[int, ...] = ()
    control_anchor: tuple[int, int, int, int] | None = None
    selection_start: int | None = None
    selection_end: int | None = None

    def __post_init__(self) -> None:
        if self.scope not in {"selection", "document"}:
            raise ValueError("Selection scope must be selection or document.")
        if self.document_digest and len(self.document_digest) != 64:
            raise ValueError("Document digest must be an empty value or a SHA-256 digest.")
        if self.control_anchor is not None:
            left, top, right, bottom = self.control_anchor
            if right <= left or bottom <= top:
                raise ValueError("Selection control anchor must have a positive size.")
        if (self.selection_start is None) != (self.selection_end is None):
            raise ValueError("Selection offsets must both be present or both be absent.")
        if self.selection_start is not None and (
            self.selection_start < 0 or self.selection_end <= self.selection_start
        ):
            raise ValueError("Selection offsets must describe a positive text range.")


def active_window() -> WindowSnapshot:
    if sys.platform != "win32":
        return WindowSnapshot(0, 0, "")
    user32 = ctypes.windll.user32
    handle = int(user32.GetForegroundWindow() or 0)
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
    length = int(user32.GetWindowTextLengthW(handle))
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(handle, buffer, length + 1)
    class_buffer = ctypes.create_unicode_buffer(256)
    try:
        user32.GetClassNameW(handle, class_buffer, len(class_buffer))
        class_name = class_buffer.value
    except (AttributeError, OSError):
        class_name = ""
    return WindowSnapshot(handle, int(process_id.value), buffer.value, class_name)


def last_input_tick() -> int:
    if sys.platform != "win32":
        return 0
    info = _LastInputInfo(ctypes.sizeof(_LastInputInfo), 0)
    if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return int(info.dwTime)
    return 0


def _no_selection_message(window: WindowSnapshot) -> str:
    """Why nothing could be captured, in the words that let a writer act.

    A document opened from a download, an email attachment or a network
    share opens in Word's Protected View. Nothing in it can be selected or
    changed, so the honest answer is not "highlight some text" - the writer
    already did - it is that the document is still read only.
    """
    title = str(window.title or "")
    if "protected view" in title.casefold():
        return (
            "This document is open in Protected View, so it cannot be "
            "selected or changed yet. Click Enable Editing in Word, then "
            "try again."
        )
    return "No selected text was found. Highlight text and try again."


def _is_word_document(window: WindowSnapshot) -> bool:
    return bool(
        window.class_name.casefold() == "opusapp"
        or " - word" in window.title.casefold()
    )


def _active_word_application():
    import comtypes.client

    return comtypes.client.GetActiveObject("Word.Application", dynamic=True)


def _canonical_word_text(text: str, *, remove_final_mark: bool = False) -> str:
    value = (
        str(text or "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        # The substitution the capture already made; see
        # windows_selection.normalize_captured_text.
        .replace(_WORD_NONBREAKING_SPACE, " ")
    )
    if remove_final_mark and value.endswith("\n"):
        value = value[:-1]
    return value


def record_covers_entire_document(record: SelectionRecord) -> bool:
    if record.scope == "document":
        return True
    if record.scope != "selection" or not record.document_digest:
        return False
    if (
        record.selection_start is not None
        and record.selection_end is not None
        and (record.selection_start != 0 or record.selection_end != len(record.text))
    ):
        return False
    return (
        hashlib.sha256(record.text.encode("utf-8")).hexdigest()
        == record.document_digest
    )

def record_uses_word_document_adapter(record: SelectionRecord) -> bool:
    stable_range = (
        record.scope == "selection"
        and bool(record.document_digest)
        and record.selection_start is not None
        and record.selection_end is not None
        and record.selection_start >= 0
        and record.selection_end == record.selection_start + len(record.text)
    )
    return (
        _is_word_document(record.window)
        and (record_covers_entire_document(record) or stable_range)
    )


def _word_visible_text_and_spans(text: str) -> tuple[str, tuple[tuple[int, int], ...]]:
    visible: list[str] = []
    spans: list[tuple[int, int]] = []
    raw = str(text or "")
    index = 0
    while index < len(raw):
        character = raw[index]
        if character in _WORD_UIA_OMITTED_CHARACTERS:
            index += 1
            continue
        if character == "\r":
            raw_end = index + 1
            if raw_end < len(raw) and raw[raw_end] == "\n":
                raw_end += 1
            visible.append("\n")
            spans.append((index, raw_end))
            index = raw_end
            continue
        visible.append(" " if character == _WORD_NONBREAKING_SPACE else character)
        spans.append((index, index + 1))
        index += 1
    value = "".join(visible)
    trimmed_length = len(value.rstrip("\n"))
    return value[:trimmed_length], tuple(spans[:trimmed_length])


def _set_foreground_window(user32, handle: int) -> bool:
    """Give a window the foreground, even when Manuscript is in the background.

    Windows refuses a foreground change from a process that does not already
    own the foreground; ``SetForegroundWindow`` just returns false and the
    window never comes forward. Attaching the calling thread to the current
    foreground thread's input queue lifts that restriction for the length of
    the call, which is the documented way to hand focus back to the window a
    user was working in.

    Accept in Word failed on exactly this. The HUD is deliberately shown
    without activation, so at the moment Accept runs the foreground belongs to
    whatever the user last clicked, Manuscript is a background process, and
    returning focus to Word was refused. The replacement then aborted with
    "target window could not be activated" before it ever reached the COM
    guards, and the user saw "The document may have been busy". The Word probe
    already did this; the app did not.
    """
    if user32.SetForegroundWindow(handle):
        return True
    # Every call below is optional: a stub user32 without them simply means the
    # plain request above was the only thing available to try.
    get_foreground = getattr(user32, "GetForegroundWindow", None)
    get_thread = getattr(user32, "GetWindowThreadProcessId", None)
    attach = getattr(user32, "AttachThreadInput", None)
    if not callable(get_foreground) or not callable(get_thread) or not callable(attach):
        return False
    foreground = get_foreground()
    if not foreground:
        return False
    this_thread = int(ctypes.windll.kernel32.GetCurrentThreadId())
    threads = (
        int(get_thread(foreground, None) or 0),
        int(get_thread(handle, None) or 0),
    )
    attached: list[int] = []
    try:
        for thread in threads:
            if thread and thread != this_thread and thread not in attached:
                if attach(this_thread, thread, True):
                    attached.append(thread)
        bring_to_top = getattr(user32, "BringWindowToTop", None)
        if callable(bring_to_top):
            bring_to_top(handle)
        return bool(user32.SetForegroundWindow(handle))
    finally:
        # Leaving input queues attached would tie Manuscript's message loop to
        # another application's; always detach, even if the call above raised.
        for thread in attached:
            attach(this_thread, thread, False)


def _word_title_names_document(window_title: str, document_name: str) -> bool:
    """Whether a Word window title refers to the document COM says is active.

    ``Document.Name`` always carries the extension - "essay.doc" - but the
    title bar only shows one when Explorer is set to show extensions for known
    file types, and Windows hides them by default. So a plain substring test
    compared "essay.doc" against "essay  -  Compatibility Mode - Word" and
    refused every replacement, on what is the out-of-the-box setting: Replace
    Text simply did nothing in Word, over and over, with no visible reason.

    Matching the stem as well costs nothing in safety. This is only a cheap
    pre-filter; the caller still verifies a SHA-256 digest of the whole
    document and compares the exact captured range before writing anything.
    """
    title = window_title.casefold()
    if not title:
        return False
    name = document_name.casefold()
    if name in title:
        return True
    stem, separator, _extension = name.rpartition(".")
    return bool(separator) and bool(stem) and stem in title


def _safe_document_name(document: object) -> str:
    try:
        return str(getattr(document, "Name", "") or "").strip()
    except Exception:  # noqa: BLE001 - Word raises assorted COM errors
        return ""


def _word_document_for_record(word: object, record: SelectionRecord) -> object | None:
    """The open document this capture came from, by name rather than by focus.

    This used to trust `word.ActiveDocument`, and that is wrong whenever more
    than one document is open: Word does not move ActiveDocument to follow the
    focused window. On 2026-08-17 every essay after the first failed for this
    reason, because the batch leaves each essay open - "refused a different
    active document (document='01_Dogs...' title='02_Cats... - Word')" - and a
    correction could never be applied again for the rest of the run.

    The guard itself was right and is kept: a correction must only ever be
    written to the document its capture came from. Refusing when nothing
    matches is deliberate, because writing into the wrong essay is far worse
    than declining to write at all.
    """
    title = record.window.title
    active = getattr(word, "ActiveDocument", None)
    active_name = _safe_document_name(active)
    if active_name and _word_title_names_document(title, active_name):
        return active
    try:
        documents = list(word.Documents)
    except Exception:  # noqa: BLE001 - Word raises assorted COM errors
        return None
    for candidate in documents:
        name = _safe_document_name(candidate)
        if name and _word_title_names_document(title, name):
            return candidate
    return None


def _replace_word_document_range(
    record: SelectionRecord,
    *,
    expected_document_text: str,
    start: int,
    end: int,
    expected_text: str,
    new_text: str,
) -> bool | object:
    """Replace one verified Word text range while preserving nearby objects."""
    if sys.platform != "win32" or not record_uses_word_document_adapter(record):
        return _WORD_COM_NOT_APPLICABLE
    try:
        word = _active_word_application()
    except Exception:
        logger.info("Word COM range adapter unavailable")
        return _WORD_COM_NOT_APPLICABLE
    try:
        document = _word_document_for_record(word, record)
        if document is None:
            logger.warning(
                "Word COM range replacement found no open document named by the "
                "capture (title=%r active=%r)",
                record.window.title,
                _safe_document_name(getattr(word, "ActiveDocument", None)),
            )
            return False
        content = document.Content
        visible_text, spans = _word_visible_text_and_spans(content.Text)
        captured_text = _canonical_word_text(expected_document_text).rstrip("\n")
        record_text = _canonical_word_text(record.text).rstrip("\n")
        if record_covers_entire_document(record):
            range_start = 0
            range_end = len(visible_text)
            current_range_text = visible_text
        else:
            range_start = int(record.selection_start or 0)
            range_end = int(record.selection_end or 0)
            # A selection that runs to the end of the document carries the
            # trailing paragraph marks with it, and `visible_text` above has
            # already dropped them, so the captured end sits a few characters
            # past this text while pointing at the same place. Clamping is safe
            # because it is not the guard: the text equality below still has to
            # hold, and it refuses anything this clamp got wrong. Refusing here
            # instead cost a writer every correction after the first.
            range_end = min(range_end, len(visible_text))
            live_digest = hashlib.sha256(visible_text.encode("utf-8")).hexdigest()
            if (
                live_digest != record.document_digest
                or range_start < 0
                or range_end > len(visible_text)
            ):
                logger.warning(
                    "Word COM range replacement refused changed document text "
                    "(digest_match=%s live=%s captured=%s doc_chars=%d "
                    "range=%d..%d)",
                    live_digest == record.document_digest,
                    live_digest[:12],
                    str(record.document_digest)[:12],
                    len(visible_text),
                    range_start,
                    range_end,
                )
                return False
            current_range_text = visible_text[range_start:range_end]
        if current_range_text != captured_text or record_text != captured_text:
            logger.warning(
                "Word COM range replacement refused changed document text "
                "(range_matches=%s record_matches=%s range_chars=%d "
                "captured_chars=%d record_chars=%d first_diff=%s)",
                current_range_text == captured_text,
                record_text == captured_text,
                len(current_range_text),
                len(captured_text),
                len(record_text),
                next(
                    (
                        f"{index}:{current_range_text[index]!r}!={captured_text[index]!r}"
                        for index in range(
                            min(len(current_range_text), len(captured_text))
                        )
                        if current_range_text[index] != captured_text[index]
                    ),
                    "length-only",
                ),
            )
            return False
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or start < 0
            or end <= start
            or end > len(captured_text)
            or captured_text[start:end] != expected_text
        ):
            logger.warning("Word COM range replacement refused a stale review range")
            return False
        absolute_start = range_start + start
        absolute_end = range_start + end
        raw_start = spans[absolute_start][0]
        raw_end = spans[absolute_end - 1][1]
        raw_range = str(content.Text or "")[raw_start:raw_end]
        if any(
            character in raw_range
            for character in _WORD_OBJECT_ANCHORS
        ):
            logger.warning(
                "Word COM range replacement refused a range containing an object"
            )
            return False
        document.Activate()
        word.Activate()
        content_start = int(getattr(content, "Start", 0) or 0)
        replacement = _canonical_word_text(new_text).replace("\n", "\r")
        document.Range(
            content_start + raw_start,
            content_start + raw_end,
        ).Text = replacement
        if not record_covers_entire_document(record):
            updated_text = (
                captured_text[:start]
                + _canonical_word_text(new_text)
                + captured_text[end:]
            )
            updated_document_text, _updated_spans = _word_visible_text_and_spans(
                document.Content.Text
            )
            return replace(
                record,
                text=updated_text,
                document_digest=hashlib.sha256(
                    updated_document_text.encode("utf-8")
                ).hexdigest(),
                selection_end=range_start + len(updated_text),
            )
        return True
    except Exception:
        logger.warning("Word COM range replacement failed", exc_info=True)
        return False

def _replace_word_document_text(
    record: SelectionRecord,
    new_text: str,
) -> bool | object:
    """Replace one verified Word document without depending on HUD focus."""
    if (
        sys.platform != "win32"
        or not record_covers_entire_document(record)
        or not _is_word_document(record.window)
    ):
        return _WORD_COM_NOT_APPLICABLE
    try:
        word = _active_word_application()
    except Exception:
        logger.info("Word COM adapter unavailable; using UI Automation replacement")
        return _WORD_COM_NOT_APPLICABLE
    try:
        document = word.ActiveDocument
        document_name = str(document.Name or "").strip()
        if not document_name or not _word_title_names_document(
            record.window.title, document_name
        ):
            logger.warning(
                "Word COM replacement refused a different active document "
                "(document=%r title=%r)",
                document_name,
                record.window.title,
            )
            return False
        content = document.Content
        source_text = _canonical_word_text(content.Text, remove_final_mark=True)
        captured_text = _canonical_word_text(record.text)
        # Word COM exposes its mandatory final paragraph mark and any trailing
        # blank paragraph. UI Automation omits those boundary marks. Ignore
        # only that known trailing-newline difference when verifying the
        # captured document; every visible character must still match.
        if source_text.rstrip("\n") != captured_text.rstrip("\n"):
            logger.warning("Word COM replacement refused changed document text")
            return False

        document.Activate()
        word.Activate()
        end = max(0, int(content.End) - 1)
        replacement = _canonical_word_text(new_text).replace("\n", "\r")
        document.Range(0, end).Text = replacement
        return True
    except Exception:
        logger.warning("Word COM replacement failed", exc_info=True)
        return False


class WindowsTextInserter:
    def __init__(self, mode: InsertionMode = InsertionMode.AUTO):
        self._last_record: InsertionRecord | None = None
        self._lock = threading.RLock()
        self._mode = InsertionMode(mode)
        self._selection_provider: Callable[[str], SelectionRecord | None] | None = None
        self._selection_restorer: Callable[[SelectionRecord], bool] | None = None

    @property
    def last_record(self) -> InsertionRecord | None:
        with self._lock:
            return self._last_record

    @property
    def mode(self) -> InsertionMode:
        with self._lock:
            return self._mode

    def set_mode(self, mode: InsertionMode) -> None:
        with self._lock:
            self._mode = InsertionMode(mode)

    def set_selection_provider(
        self,
        provider: Callable[[str], SelectionRecord | None] | None,
    ) -> None:
        with self._lock:
            self._selection_provider = provider

    def set_selection_restorer(
        self,
        restorer: Callable[[SelectionRecord], bool] | None,
    ) -> None:
        with self._lock:
            self._selection_restorer = restorer

    def activate_window(self, window: WindowSnapshot) -> bool:
        return self._activate(window)

    def insert(
        self,
        text: str,
        *,
        expected_window: WindowSnapshot | None = None,
        _mode_override: InsertionMode | None = None,
        _restore_clipboard_before_return: bool = False,
    ) -> InsertionRecord | None:
        if not text:
            return None
        if sys.platform != "win32":
            raise RuntimeError("Text insertion requires Windows.")
        window = active_window()
        target = expected_window or window
        if not _same_window(window, target):
            return None
        with self._lock:
            mode = InsertionMode(_mode_override) if _mode_override is not None else self._mode
        old_clipboard: str | object = _CLIPBOARD_UNAVAILABLE
        if mode is not InsertionMode.DIRECT:
            try:
                old_clipboard = pyperclip.paste()
            except Exception:
                logger.debug("Clipboard backup failed", exc_info=True)
        clipboard_staged = False

        def restore_staged_clipboard() -> None:
            nonlocal clipboard_staged
            if not clipboard_staged or old_clipboard is _CLIPBOARD_UNAVAILABLE:
                return
            if _restore_clipboard_before_return:
                if not _restore_clipboard_with_retry(str(old_clipboard), text):
                    logger.warning(
                        "Windows clipboard changed before Manuscript could restore it"
                    )
            else:
                _schedule_clipboard_restore(str(old_clipboard), text)
            clipboard_staged = False

        try:
            _release_stuck_modifiers()
            time.sleep(0.04)
            if not _target_is_active(target):
                return None

            if mode is InsertionMode.DIRECT:
                if not _send_unicode_text(text, expected_window=target):
                    return None
            elif old_clipboard is not _CLIPBOARD_UNAVAILABLE:
                clipboard_staged = _stage_clipboard(text)
            else:
                if mode is InsertionMode.CLIPBOARD:
                    raise RuntimeError(
                        "Clipboard insertion is selected, but the clipboard could not be read."
                    )

            if mode is not InsertionMode.DIRECT:
                if clipboard_staged:
                    if not _target_is_active(target):
                        restore_staged_clipboard()
                        return None
                    _send_combo((VK_CONTROL, VK_V))
                    paste_settle = min(
                        _CLIPBOARD_PASTE_MAX_SETTLE_SECONDS,
                        _CLIPBOARD_PASTE_BASE_SETTLE_SECONDS
                        + (len(text) * _CLIPBOARD_PASTE_PER_CHARACTER_SECONDS),
                    )
                    if (
                        _restore_clipboard_before_return
                        and _is_office_paste_target(target)
                    ):
                        paste_settle = max(
                            paste_settle,
                            _OFFICE_REPLACEMENT_SETTLE_SECONDS,
                        )
                    time.sleep(paste_settle)
                elif mode is InsertionMode.CLIPBOARD:
                    _restore_clipboard(str(old_clipboard), text)
                    raise RuntimeError(
                        "Clipboard insertion is selected, but text could not be staged."
                    )
                else:
                    _restore_clipboard(str(old_clipboard), text)
                    if not _send_unicode_text(text, expected_window=target):
                        return None

            record = InsertionRecord(text, window, last_input_tick(), time.monotonic())
            with self._lock:
                self._last_record = record
            restore_staged_clipboard()
            return record
        except PartialTextInsertionError:
            restore_staged_clipboard()
            raise
        except Exception as exc:
            restore_staged_clipboard()
            raise RuntimeError(f"Manuscript could not insert text: {exc}") from exc

    def insert_into(self, window: WindowSnapshot, text: str) -> InsertionRecord | None:
        if sys.platform != "win32":
            raise RuntimeError("Text insertion requires Windows.")
        for attempt in range(_TARGET_INSERT_ATTEMPTS):
            if self._activate(window):
                time.sleep(0.05)
                record = self.insert(text, expected_window=window)
                if record is not None:
                    return record
            if attempt + 1 < _TARGET_INSERT_ATTEMPTS:
                time.sleep(_TARGET_INSERT_RETRY_SECONDS)
        return None

    def capture_selection(self) -> SelectionRecord:
        if sys.platform != "win32":
            raise RuntimeError("Selected-text commands require Windows.")
        window = active_window()
        if not window.handle:
            raise RuntimeError("No active text window was found.")
        marker = f"ECHOLINK_SELECTION_{time.monotonic_ns()}"
        try:
            old_clipboard = str(pyperclip.paste())
        except Exception as exc:
            raise RuntimeError(
                "Manuscript could not protect the Windows clipboard. "
                "Nothing was captured. Close clipboard tools and try again."
            ) from exc
        _release_stuck_modifiers()
        if not _stage_clipboard(marker):
            raise RuntimeError(
                "Manuscript could not safely stage selected-text capture. Nothing was captured."
            )
        _send_combo((VK_CONTROL, VK_C))
        deadline = time.monotonic() + 0.6
        selected = marker
        while time.monotonic() < deadline:
            try:
                selected = pyperclip.paste()
            except Exception:
                selected = marker
            if selected != marker:
                break
            time.sleep(0.015)
        if not _restore_clipboard_with_retry(old_clipboard, str(selected)):
            raise RuntimeError(
                "The Windows clipboard changed during capture. Manuscript left the newer "
                "clipboard content alone. Highlight the text and try again."
            )
        if selected == marker or not str(selected).strip():
            raise RuntimeError(_no_selection_message(window))
        return SelectionRecord(str(selected), window, "selection")

    def replace_selection(self, record: SelectionRecord, new_text: str) -> InsertionRecord | None:
        if not new_text.strip():
            raise ValueError("Replacement text is empty.")
        word_result = _replace_word_document_text(record, new_text)
        if word_result is True:
            inserted = InsertionRecord(
                new_text,
                record.window,
                last_input_tick(),
                time.monotonic(),
            )
            with self._lock:
                self._last_record = inserted
            return inserted
        if word_result is False:
            return None
        if not self._activate(record.window):
            logger.warning(
                "Replacement recheck failed: target window %s could not be activated",
                record.window.handle,
            )
            return None
        with self._lock:
            provider = self._selection_provider
            restorer = self._selection_restorer
        current = None
        if provider is not None:
            try:
                current = provider(record.scope)
            except Exception:
                logger.warning("UI Automation replacement recheck failed", exc_info=True)
        if current is None and restorer is not None:
            for attempt in range(_SELECTION_RESTORE_ATTEMPTS):
                try:
                    restored = restorer(record)
                except Exception:
                    restored = False
                    logger.warning("UI Automation selection recovery failed", exc_info=True)
                if restored and provider is not None:
                    try:
                        current = provider(record.scope)
                    except Exception:
                        logger.warning("Recovered selection recheck failed", exc_info=True)
                if current is not None:
                    break
                if attempt + 1 < _SELECTION_RESTORE_ATTEMPTS:
                    self._activate(record.window)
                    time.sleep(_SELECTION_RESTORE_RETRY_SECONDS)
        if current is None and record.scope == "selection":
            current = self.capture_selection()
        if current is None:
            logger.warning(
                "Replacement recheck failed: no %s could be read from window %s",
                record.scope,
                record.window.handle,
            )
            return None
        if current.window.handle != record.window.handle:
            logger.warning(
                "Replacement recheck failed: window changed from %s to %s",
                record.window.handle,
                current.window.handle,
            )
            return None
        # Editors report different line endings than the text that was pasted
        # into them (Notepad turns "\n" into "\r"), so the equality check must
        # compare content, not byte-for-byte newlines.
        newline_normalized_match = _normalize_newlines(
            current.text
        ) == _normalize_newlines(record.text)
        if current.text != record.text and not newline_normalized_match:
            logger.warning(
                "Replacement recheck failed: %s text changed, expected %d chars "
                "got %d chars (no content is logged)",
                record.scope,
                len(record.text),
                len(current.text),
            )
            return None
        if current.scope != record.scope:
            logger.warning(
                "Replacement recheck failed: scope changed from %s to %s",
                record.scope,
                current.scope,
            )
            return None
        if (
            record.document_digest
            and current.document_digest != record.document_digest
            and not newline_normalized_match
        ):
            logger.warning(
                "Replacement recheck failed: document digest changed",
            )
            return None
        if record.scope == "document":
            _send_combo((VK_CONTROL, VK_A))
            time.sleep(0.04)
            if not _target_is_active(record.window):
                return None
        # Rewrites can contain several paragraphs. Sending those one character at a
        # time is unreliable in Windows editors, so stage one paste and restore the
        # user's clipboard immediately afterward.
        return self.insert(
            new_text,
            expected_window=record.window,
            _mode_override=InsertionMode.CLIPBOARD,
            _restore_clipboard_before_return=True,
        )

    def replace_document_range(
        self,
        record: SelectionRecord,
        *,
        expected_document_text: str,
        start: int,
        end: int,
        expected_text: str,
        new_text: str,
    ) -> InsertionRecord | SelectionRecord | None:
        if not new_text.strip():
            raise ValueError("Replacement text is empty.")
        result = _replace_word_document_range(
            record,
            expected_document_text=expected_document_text,
            start=start,
            end=end,
            expected_text=expected_text,
            new_text=new_text,
        )
        if isinstance(result, SelectionRecord):
            return result
        if result is not True:
            return None
        inserted = InsertionRecord(
            new_text,
            record.window,
            last_input_tick(),
            time.monotonic(),
        )
        with self._lock:
            self._last_record = inserted
        return inserted
    @staticmethod
    def _activate(window: WindowSnapshot) -> bool:
        if sys.platform != "win32":
            return False
        current = active_window()
        if current.handle == window.handle and current.process_id == window.process_id:
            return True
        user32 = ctypes.windll.user32
        if not window.handle or not user32.IsWindow(window.handle):
            return False
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window.handle, ctypes.byref(process_id))
        if int(process_id.value) != window.process_id:
            return False
        # SW_RESTORE also unmaximizes a maximized editor. Only use it when the
        # captured window is actually minimized; otherwise return focus without
        # changing the user's window size or placement.
        is_iconic = getattr(user32, "IsIconic", None)
        if callable(is_iconic) and bool(is_iconic(window.handle)):
            user32.ShowWindow(window.handle, 9)
        _set_foreground_window(user32, int(window.handle))
        deadline = time.monotonic() + 0.75
        while time.monotonic() < deadline:
            current = active_window()
            if current.handle == window.handle and current.process_id == window.process_id:
                return True
            time.sleep(0.02)
        return False

    def replace_if_safe(self, record: InsertionRecord, new_text: str) -> InsertionRecord | None:
        if not new_text or new_text == record.text:
            return record
        current = active_window()
        if current.handle != record.window.handle or current.process_id != record.window.process_id:
            return None
        if last_input_tick() != record.input_tick:
            return None
        _send_combo((VK_CONTROL, VK_Z))
        time.sleep(0.06)
        return self.insert(new_text, expected_window=record.window)

    def undo_last(self, max_age_seconds: float = 120.0) -> bool:
        with self._lock:
            record = self._last_record
        if record is None or time.monotonic() - record.inserted_at > max_age_seconds:
            return False
        current = active_window()
        if current.handle != record.window.handle or current.process_id != record.window.process_id:
            return False
        _send_combo((VK_CONTROL, VK_Z))
        with self._lock:
            self._last_record = None
        return True


def _send_combo(keys: tuple[int, ...]) -> None:
    for key in keys:
        _send_key(key, up=False)
    time.sleep(0.01)
    for key in reversed(keys):
        _send_key(key, up=True)


def _send_unicode_text(text: str, *, expected_window: WindowSnapshot) -> bool:
    if any(ord(character) > 0xFFFF for character in text):
        raise RuntimeError(
            "Direct Unicode input cannot safely preserve supplementary characters such as "
            "emoji in every Windows app. Use Auto or Clipboard insertion for this text."
        )
    sent_units = 0
    for unit_count, payloads in _direct_input_steps(text):
        if not _target_is_active(expected_window):
            if sent_units:
                raise PartialTextInsertionError(
                    text,
                    sent_units,
                    reason="Target focus changed during direct text insertion.",
                )
            return False
        try:
            _send_inputs(payloads, operation="direct Unicode input")
        except SendInputError as exc:
            touched_total = sent_units + (unit_count if exc.sent_events else 0)
            if touched_total:
                raise PartialTextInsertionError(
                    text,
                    touched_total,
                    reason="Direct text insertion stopped partway through.",
                ) from exc
            raise
        sent_units += unit_count
        if _DIRECT_CHARACTER_DELAY_SECONDS > 0:
            time.sleep(_DIRECT_CHARACTER_DELAY_SECONDS)
    return True


def _direct_input_steps(text: str):
    index = 0
    while index < len(text):
        character = text[index]
        if character == "\r":
            unit_count = 2 if index + 1 < len(text) and text[index + 1] == "\n" else 1
            index += unit_count
            yield unit_count, [
                _keyboard_payload(key=VK_SHIFT),
                _keyboard_payload(key=VK_RETURN),
                _keyboard_payload(key=VK_RETURN, flags=KEYEVENTF_KEYUP),
                _keyboard_payload(key=VK_SHIFT, flags=KEYEVENTF_KEYUP),
            ]
            continue
        if character == "\n":
            index += 1
            yield 1, [
                _keyboard_payload(key=VK_SHIFT),
                _keyboard_payload(key=VK_RETURN),
                _keyboard_payload(key=VK_RETURN, flags=KEYEVENTF_KEYUP),
                _keyboard_payload(key=VK_SHIFT, flags=KEYEVENTF_KEYUP),
            ]
            continue
        encoded = character.encode("utf-16-le", errors="surrogatepass")
        units = [
            int.from_bytes(encoded[offset : offset + 2], "little")
            for offset in range(0, len(encoded), 2)
        ]
        payloads: list[_Input] = []
        for unit in units:
            payloads.append(_keyboard_payload(scan=unit, flags=KEYEVENTF_UNICODE))
            payloads.append(
                _keyboard_payload(
                    scan=unit,
                    flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                )
            )
        index += 1
        yield len(units), payloads


def _keyboard_payload(*, key: int = 0, scan: int = 0, flags: int = 0) -> _Input:
    union = _InputUnion()
    union.ki = _KeyboardInput(key, scan, flags, 0, 0)
    return _Input(INPUT_KEYBOARD, union)


def _send_inputs(payloads: list[_Input], *, operation: str) -> None:
    if not payloads:
        return
    payload_array = (_Input * len(payloads))(*payloads)
    user32 = ctypes.windll.user32
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    ctypes.set_last_error(0)
    sent = int(user32.SendInput(len(payloads), payload_array, ctypes.sizeof(_Input)))
    if sent != len(payloads):
        error_code = int(ctypes.windll.kernel32.GetLastError())
        detail = ctypes.FormatError(error_code).strip() if error_code else "Unknown Windows error."
        raise SendInputError(
            f"SendInput sent {sent} of {len(payloads)} events for {operation} "
            f"(Windows error {error_code}: {detail}). Windows may block input across an "
            "elevated-app UIPI boundary; Manuscript does not bypass that protection.",
            sent_events=sent,
            requested_events=len(payloads),
        )


def _utf16_unit_count(text: str) -> int:
    return len(text.encode("utf-16-le", errors="surrogatepass")) // 2


def _split_after_utf16_units(text: str, touched_units: int) -> tuple[str, str]:
    if touched_units <= 0:
        return "", text
    consumed = 0
    for index, character in enumerate(text):
        consumed += _utf16_unit_count(character)
        if consumed >= touched_units:
            return text[: index + 1], text[index + 1 :]
    return text, ""


def _release_stuck_modifiers(timeout: float = 0.35) -> None:
    if sys.platform != "win32":
        return
    user32 = ctypes.windll.user32
    deadline = time.monotonic() + max(0.0, float(timeout))
    while time.monotonic() < deadline:
        if not any(user32.GetAsyncKeyState(key) & 0x8000 for key in MODIFIER_KEYS):
            return
        time.sleep(0.01)
    for key in MODIFIER_KEYS:
        if user32.GetAsyncKeyState(key) & 0x8000:
            _send_key(key, up=True)


def _send_key(key: int, *, up: bool) -> None:
    payload = _keyboard_payload(key=key, flags=KEYEVENTF_KEYUP if up else 0)
    _send_inputs([payload], operation=f"key {key}")


def _same_window(left: WindowSnapshot, right: WindowSnapshot) -> bool:
    return bool(
        left.handle
        and right.handle
        and left.handle == right.handle
        and left.process_id == right.process_id
    )


def _target_is_active(window: WindowSnapshot) -> bool:
    return _same_window(active_window(), window)


def _is_office_paste_target(window: WindowSnapshot) -> bool:
    class_name = window.class_name.casefold()
    if class_name in {"opusapp", "_wwb", "rctrl_renwnd32"}:
        return True
    title = window.title.casefold()
    return bool(
        " - word" in title
        or " - message (" in title
        or title.endswith(" - appointment")
        or title.endswith(" - meeting")
    )


def _stage_clipboard(text: str) -> bool:
    try:
        copy_text_private(text)
    except Exception:
        logger.debug("Clipboard staging failed", exc_info=True)
        return False
    for _attempt in range(20):
        try:
            if pyperclip.paste() == text:
                return True
        except Exception:
            pass
        time.sleep(0.005)
    logger.debug("Clipboard text could not be verified; using direct input")
    return False


def _restore_clipboard(value: str, expected_current: str) -> bool | None:
    try:
        current = pyperclip.paste()
    except Exception:
        logger.debug("Clipboard restore read failed", exc_info=True)
        return None
    if current != expected_current:
        return False
    try:
        pyperclip.copy(value)
    except Exception:
        logger.debug("Clipboard restore write failed", exc_info=True)
        return None
    return True


def _restore_clipboard_with_retry(
    value: str,
    expected_current: str,
    *,
    timeout: float = _CLIPBOARD_RESTORE_TIMEOUT_SECONDS,
    retry_interval: float = _CLIPBOARD_RESTORE_RETRY_SECONDS,
) -> bool:
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        restored = _restore_clipboard(value, expected_current)
        if restored is not None:
            return restored
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(max(0.0, float(retry_interval)), remaining))


def _schedule_clipboard_restore(value: str, expected_current: str) -> None:
    timer = threading.Timer(
        _CLIPBOARD_RESTORE_DELAY_SECONDS,
        _restore_clipboard_with_retry,
        args=(value, expected_current),
    )
    timer.daemon = True
    timer.start()
