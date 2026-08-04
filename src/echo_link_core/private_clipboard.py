from __future__ import annotations

import ctypes
import sys
import time
from dataclasses import dataclass
from ctypes import wintypes
from typing import Protocol


CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
EXCLUDE_MONITOR_FORMAT = "ExcludeClipboardContentFromMonitorProcessing"
INCLUDE_HISTORY_FORMAT = "CanIncludeInClipboardHistory"
UPLOAD_CLOUD_FORMAT = "CanUploadToCloudClipboard"


class ClipboardAPI(Protocol):
    def register_format(self, name: str) -> int: ...
    def allocate(self, payload: bytes) -> int: ...
    def open(self) -> bool: ...
    def empty(self) -> bool: ...
    def set_data(self, format_id: int, handle: int) -> bool: ...
    def close(self) -> None: ...
    def free(self, handle: int) -> None: ...
    def error(self, message: str) -> OSError: ...


@dataclass(frozen=True, slots=True)
class ClipboardPrivacyReceipt:
    history_excluded: bool
    cloud_upload_excluded: bool
    monitor_processing_excluded: bool


class _WindowsClipboardAPI:
    def __init__(self) -> None:
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        self.user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
        self.user32.RegisterClipboardFormatW.restype = wintypes.UINT
        self.user32.OpenClipboard.argtypes = [wintypes.HWND]
        self.user32.OpenClipboard.restype = wintypes.BOOL
        self.user32.EmptyClipboard.argtypes = []
        self.user32.EmptyClipboard.restype = wintypes.BOOL
        self.user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        self.user32.SetClipboardData.restype = wintypes.HANDLE
        self.user32.CloseClipboard.argtypes = []
        self.user32.CloseClipboard.restype = wintypes.BOOL

        self.kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        self.kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        self.kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        self.kernel32.GlobalLock.restype = ctypes.c_void_p
        self.kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        self.kernel32.GlobalUnlock.restype = wintypes.BOOL
        self.kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
        self.kernel32.GlobalFree.restype = wintypes.HGLOBAL

    def register_format(self, name: str) -> int:
        return int(self.user32.RegisterClipboardFormatW(name) or 0)

    def allocate(self, payload: bytes) -> int:
        handle = int(self.kernel32.GlobalAlloc(GMEM_MOVEABLE, len(payload)) or 0)
        if not handle:
            raise self.error("Windows could not allocate private clipboard data")
        pointer = self.kernel32.GlobalLock(handle)
        if not pointer:
            self.kernel32.GlobalFree(handle)
            raise self.error("Windows could not lock private clipboard data")
        try:
            ctypes.memmove(pointer, payload, len(payload))
        finally:
            self.kernel32.GlobalUnlock(handle)
        return handle

    def open(self) -> bool:
        return bool(self.user32.OpenClipboard(None))

    def empty(self) -> bool:
        return bool(self.user32.EmptyClipboard())

    def set_data(self, format_id: int, handle: int) -> bool:
        return bool(self.user32.SetClipboardData(format_id, handle))

    def close(self) -> None:
        self.user32.CloseClipboard()

    def free(self, handle: int) -> None:
        self.kernel32.GlobalFree(handle)

    def error(self, message: str) -> OSError:
        return ctypes.WinError(ctypes.get_last_error(), message)


def copy_text_private(
    text: str,
    *,
    _api: ClipboardAPI | None = None,
    attempts: int = 20,
    retry_seconds: float = 0.005,
) -> ClipboardPrivacyReceipt:
    """Stage Unicode text while excluding it from Windows history and cloud sync."""
    if sys.platform != "win32" and _api is None:
        raise RuntimeError("Private clipboard staging requires Windows.")
    if "\x00" in text:
        raise ValueError("Clipboard text cannot contain a null character.")

    api = _api or _WindowsClipboardAPI()
    registered = {
        EXCLUDE_MONITOR_FORMAT: api.register_format(EXCLUDE_MONITOR_FORMAT),
        INCLUDE_HISTORY_FORMAT: api.register_format(INCLUDE_HISTORY_FORMAT),
        UPLOAD_CLOUD_FORMAT: api.register_format(UPLOAD_CLOUD_FORMAT),
    }
    if not all(registered.values()):
        raise api.error("Windows could not register private clipboard formats")

    zero = (0).to_bytes(4, byteorder="little", signed=False)
    payloads = [
        (registered[EXCLUDE_MONITOR_FORMAT], zero),
        (registered[INCLUDE_HISTORY_FORMAT], zero),
        (registered[UPLOAD_CLOUD_FORMAT], zero),
        (CF_UNICODETEXT, text.encode("utf-16-le") + b"\x00\x00"),
    ]
    handles = [(format_id, api.allocate(payload)) for format_id, payload in payloads]
    transferred: set[int] = set()
    opened = False
    try:
        for attempt in range(max(1, int(attempts))):
            if api.open():
                opened = True
                break
            if attempt + 1 < max(1, int(attempts)):
                time.sleep(max(0.0, float(retry_seconds)))
        if not opened:
            raise api.error("Windows clipboard is busy")
        if not api.empty():
            raise api.error("Windows could not clear the clipboard for private staging")

        # Privacy markers go first. Sensitive text is never transferred if a marker fails.
        for format_id, handle in handles:
            if not api.set_data(format_id, handle):
                raise api.error("Windows rejected private clipboard data")
            transferred.add(handle)
    finally:
        if opened:
            api.close()
        for _format_id, handle in handles:
            if handle not in transferred:
                api.free(handle)

    return ClipboardPrivacyReceipt(True, True, True)
