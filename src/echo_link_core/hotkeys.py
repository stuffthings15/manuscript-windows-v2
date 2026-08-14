from __future__ import annotations

import ctypes
import logging
import queue
import sys
import threading
from dataclasses import dataclass
from ctypes import wintypes
from typing import Callable


logger = logging.getLogger(__name__)

VK_MAP = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "shift": 0x10,
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "escape": 0x1B,
    "esc": 0x1B,
    "space": 0x20,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "delete": 0x2E,
    "win": 0x5B,
    "windows": 0x5B,
}
for _index in range(1, 13):
    VK_MAP[f"f{_index}"] = 0x6F + _index


@dataclass(frozen=True, slots=True)
class HotkeySpec:
    keys: frozenset[int]
    canonical: str


def parse_hotkey(value: str, *, minimum_keys: int = 2) -> HotkeySpec:
    parts = [part.strip().lower() for part in (value or "").split("+") if part.strip()]
    if not parts:
        raise ValueError("A hotkey is required.")
    keys: set[int] = set()
    canonical: list[str] = []
    aliases = {"control": "ctrl", "windows": "win", "esc": "escape"}
    for part in parts:
        part = aliases.get(part, part)
        if len(part) == 1 and part.isalnum():
            key = ord(part.upper())
        else:
            key = VK_MAP.get(part)
        if key is None:
            raise ValueError(f"Unsupported hotkey key: {part}")
        keys.add(key)
        if part not in canonical:
            canonical.append(part)
    if len(keys) < int(minimum_keys):
        if minimum_keys <= 1:
            raise ValueError("A hotkey is required.")
        raise ValueError("Use at least two keys for a global hotkey.")
    return HotkeySpec(frozenset(keys), "+".join(canonical))


def hotkeys_overlap(left: str, right: str) -> bool:
    left_keys = parse_hotkey(left).keys
    right_keys = parse_hotkey(right).keys
    return left_keys.issubset(right_keys) or right_keys.issubset(left_keys)


class _KeyboardHookStruct(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


_HOOKPROC = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
    ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)

_MODIFIER_NORMALIZATION = {
    0xA0: VK_MAP["shift"],
    0xA1: VK_MAP["shift"],
    0xA2: VK_MAP["ctrl"],
    0xA3: VK_MAP["ctrl"],
    0xA4: VK_MAP["alt"],
    0xA5: VK_MAP["alt"],
    0x5B: VK_MAP["win"],
    0x5C: VK_MAP["win"],
}
_MODIFIER_KEYS = frozenset(
    {VK_MAP["shift"], VK_MAP["ctrl"], VK_MAP["alt"], VK_MAP["win"]}
)
_STOP_CALLBACKS = object()


def normalize_hotkey_key(key: int) -> int:
    return _MODIFIER_NORMALIZATION.get(int(key), int(key))


class GlobalPushToTalk(threading.Thread):
    WH_KEYBOARD_LL = 13
    WM_KEYDOWN = 0x0100
    WM_KEYUP = 0x0101
    WM_SYSKEYDOWN = 0x0104
    WM_SYSKEYUP = 0x0105
    WM_QUIT = 0x0012

    def __init__(
        self,
        hotkey: str,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        on_error: Callable[[str], None] | None = None,
        *,
        allow_single_key: bool = False,
    ):
        super().__init__(daemon=True, name="echo-link-hotkey")
        self.spec = parse_hotkey(hotkey, minimum_keys=1 if allow_single_key else 2)
        self.on_press = on_press
        self.on_release = on_release
        self.on_error = on_error
        self._pressed: set[int] = set()
        self._active = False
        self._running = threading.Event()
        self._startup_complete = threading.Event()
        self._registration_error = ""
        self._hook = None
        self._callback_ref = None
        self._thread_id = 0
        self._callback_queue: queue.Queue[Callable[[], None] | object] = queue.Queue()
        self._callback_thread: threading.Thread | None = None
        self._callback_lock = threading.Lock()
        self._callbacks_accepting = threading.Event()

    def run(self) -> None:
        if sys.platform != "win32":
            self._report_error("Global hotkeys require Windows.")
            return
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        _configure_windows_api(user32, kernel32)
        self._thread_id = int(kernel32.GetCurrentThreadId())
        self._callback_ref = _HOOKPROC(self._hook_callback)
        self._hook = user32.SetWindowsHookExW(
            self.WH_KEYBOARD_LL,
            self._callback_ref,
            None,
            0,
        )
        if not self._hook:
            error_code = int(kernel32.GetLastError())
            detail = ctypes.FormatError(error_code).strip() if error_code else "Unknown Windows error."
            self._registration_error = (
                f"Manuscript could not register the global dictation hotkey "
                f"(Windows error {error_code}: {detail})"
            )
            self._startup_complete.set()
            self._report_error(self._registration_error)
            return
        self._start_callback_dispatcher()
        self._running.set()
        self._startup_complete.set()
        message = wintypes.MSG()
        try:
            while self._running.is_set():
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result in (0, -1):
                    break
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        finally:
            self._callbacks_accepting.clear()
            if self._hook:
                user32.UnhookWindowsHookEx(self._hook)
                self._hook = None
            self._running.clear()
            self._stop_callback_dispatcher()

    def _hook_callback(self, code, message, data_pointer):
        swallow = False
        try:
            if code >= 0:
                data = ctypes.cast(data_pointer, ctypes.POINTER(_KeyboardHookStruct)).contents
                key = int(data.vkCode)
                if message in (self.WM_KEYDOWN, self.WM_SYSKEYDOWN):
                    self._pressed.add(key)
                    pressed = {normalize_hotkey_key(value) for value in self._pressed}
                    if self.spec.keys.issubset(pressed):
                        swallow = self._is_trigger(key)
                    if not self._active and self.spec.keys.issubset(pressed):
                        self._active = True
                        self._queue_callback(self.on_press)
                elif message in (self.WM_KEYUP, self.WM_SYSKEYUP):
                    was_active = self._active
                    self._pressed.discard(key)
                    pressed = {normalize_hotkey_key(value) for value in self._pressed}
                    if was_active and self._is_trigger(key):
                        swallow = True
                    if self._active and not self.spec.keys.issubset(pressed):
                        self._active = False
                        self._queue_callback(self.on_release)
        except Exception:
            logger.exception("Global hotkey callback failed")
        if swallow:
            # Stop the chord reaching the focused application. The default
            # hotkey is ctrl+shift+space, which is also Microsoft Word's
            # shortcut for a non-breaking space: passing it through inserted
            # one into the document at the caret, the capture no longer matched
            # what Word held, and every replacement after that was correctly
            # refused. It cost 19 of 20 essays in the 2026-08-13 batch and had
            # been misdiagnosed twice before that.
            #
            # Only the trigger key is swallowed, never the modifiers, so
            # releasing ctrl or shift still behaves normally everywhere else.
            return 1
        return ctypes.windll.user32.CallNextHookEx(self._hook, code, message, data_pointer)

    def _is_trigger(self, key: int) -> bool:
        """True for the chord's non-modifier key, the one that completes it."""
        normalized = normalize_hotkey_key(key)
        return normalized in self.spec.keys and normalized not in _MODIFIER_KEYS

    def stop(self) -> None:
        self._running.clear()
        self._callbacks_accepting.clear()
        if self._thread_id and sys.platform == "win32":
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, self.WM_QUIT, 0, 0)

    def _start_callback_dispatcher(self) -> None:
        with self._callback_lock:
            if self._callback_thread is not None and self._callback_thread.is_alive():
                return
            self._callbacks_accepting.set()
            self._callback_thread = threading.Thread(
                target=self._dispatch_callbacks,
                daemon=True,
                name=f"{self.name}-callbacks",
            )
            self._callback_thread.start()

    def _queue_callback(self, callback: Callable[[], None]) -> None:
        if self._callbacks_accepting.is_set():
            self._callback_queue.put(callback)

    def _dispatch_callbacks(self) -> None:
        while True:
            callback = self._callback_queue.get()
            if callback is _STOP_CALLBACKS:
                return
            try:
                callback()
            except Exception:
                logger.exception("Global hotkey action failed")

    def _stop_callback_dispatcher(self) -> None:
        self._callbacks_accepting.clear()
        with self._callback_lock:
            thread = self._callback_thread
            if thread is None:
                return
            self._callback_thread = None
            self._callback_queue.put(_STOP_CALLBACKS)
        if thread is not threading.current_thread():
            thread.join(timeout=1.0)

    @property
    def registered(self) -> bool:
        return self._running.is_set() and bool(self._hook)

    @property
    def registration_error(self) -> str:
        return self._registration_error

    def wait_until_ready(self, timeout: float = 1.0) -> bool:
        self._startup_complete.wait(max(0.0, float(timeout)))
        return self.registered

    def _report_error(self, message: str) -> None:
        logger.error(message)
        if self.on_error:
            try:
                self.on_error(message)
            except Exception:
                logger.debug("Hotkey error callback failed", exc_info=True)


def probe_hotkey_registration(hotkey: str, timeout: float = 1.0) -> dict[str, object]:
    errors: list[str] = []
    listener = GlobalPushToTalk(
        hotkey,
        lambda: None,
        lambda: None,
        errors.append,
    )
    try:
        listener.start()
        registered = listener.wait_until_ready(timeout=timeout)
        return {
            "registered": bool(registered),
            "error": listener.registration_error or (errors[-1] if errors else ""),
        }
    finally:
        listener.stop()
        listener.join(timeout=max(0.25, float(timeout)))


def _configure_windows_api(user32, kernel32) -> None:
    user32.SetWindowsHookExW.argtypes = [
        ctypes.c_int,
        _HOOKPROC,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    user32.SetWindowsHookExW.restype = ctypes.c_void_p
    user32.CallNextHookEx.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.CallNextHookEx.restype = ctypes.c_ssize_t
    user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
    user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
    ]
    user32.GetMessageW.restype = wintypes.BOOL
    user32.PostThreadMessageW.argtypes = [
        wintypes.DWORD,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.PostThreadMessageW.restype = wintypes.BOOL
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    kernel32.GetLastError.restype = wintypes.DWORD
