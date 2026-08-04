from __future__ import annotations

import ctypes
import sys

from echo_link_app.branding import APP_USER_MODEL_ID
from typing import Any

from ctypes import wintypes


APP_MUTEX_NAME = "EchoLink.App.71099095-BCB5-4E9C-A623-D0C474335542"


def configure_windows_app_identity() -> None:
    """Give taskbar shortcuts and notifications one stable product identity."""

    if sys.platform != "win32":
        return
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    set_app_id = shell32.SetCurrentProcessExplicitAppUserModelID
    set_app_id.argtypes = [wintypes.LPCWSTR]
    set_app_id.restype = wintypes.LONG
    result = int(set_app_id(APP_USER_MODEL_ID))
    if result != 0:
        raise OSError(result, "Could not set the Manuscript AppUserModelID.")


def create_installer_mutex() -> Any | None:
    """Create the named mutex consumed by Inno Setup's AppMutex check."""

    if sys.platform != "win32":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    create_mutex.restype = wintypes.HANDLE
    handle = create_mutex(None, False, APP_MUTEX_NAME)
    if not handle:
        raise ctypes.WinError()
    return handle


def close_installer_mutex(handle: Any | None) -> None:
    if sys.platform == "win32" and handle:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        close_handle(handle)


__all__ = [
    "APP_MUTEX_NAME",
    "APP_USER_MODEL_ID",
    "close_installer_mutex",
    "configure_windows_app_identity",
    "create_installer_mutex",
]
