from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes

import pyperclip


VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_SPACE = 0x20
VK_A = 0x41
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002


def send_key(key: int, up: bool = False) -> None:
    ctypes.windll.user32.keybd_event(key, 0, KEYEVENTF_KEYUP if up else 0, 0)


def combo(*keys: int) -> None:
    for key in keys:
        send_key(key)
    time.sleep(0.04)
    for key in reversed(keys):
        send_key(key, True)


def window_for_pid(pid: int) -> int:
    found = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(handle, _extra):
        process_id = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
        if int(process_id.value) == pid and ctypes.windll.user32.IsWindowVisible(handle):
            found.append(int(handle))
            return False
        return True

    ctypes.windll.user32.EnumWindows(callback_type(visit), 0)
    return found[0] if found else 0


def titled_window(expected_title: str, *, starts_with: bool = False) -> int:
    found = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(handle, _extra):
        length = ctypes.windll.user32.GetWindowTextLengthW(handle)
        text = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(handle, text, length + 1)
        matched = (
            text.value.startswith(expected_title)
            if starts_with
            else text.value == expected_title
        )
        if matched:
            found.append(int(handle))
            return False
        return True

    ctypes.windll.user32.EnumWindows(callback_type(visit), 0)
    return found[0] if found else 0


def notepad_window() -> int:
    found = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(handle, _extra):
        length = ctypes.windll.user32.GetWindowTextLengthW(handle)
        text = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(handle, text, length + 1)
        if ctypes.windll.user32.IsWindowVisible(handle) and "Notepad" in text.value:
            found.append(int(handle))
            return False
        return True

    ctypes.windll.user32.EnumWindows(callback_type(visit), 0)
    return found[0] if found else 0


def main() -> int:
    if sys.platform != "win32":
        print("Windows only")
        return 2
    echo = titled_window("Manuscript")
    if not echo:
        print("Manuscript window not found")
        return 3
    ctypes.windll.user32.ShowWindow(echo, 5)
    ctypes.windll.user32.PostMessageW(echo, 0x0010, 0, 0)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and ctypes.windll.user32.IsWindowVisible(echo):
        time.sleep(0.05)
    note = subprocess.Popen(["notepad.exe"])
    try:
        deadline = time.monotonic() + 5.0
        handle = 0
        while time.monotonic() < deadline and not handle:
            handle = window_for_pid(note.pid) or notepad_window()
            time.sleep(0.05)
        if not handle:
            print("Notepad window not found")
            return 4
        ctypes.windll.user32.SetForegroundWindow(handle)
        pyperclip.copy("Manuscript selected-text hotkey probe")
        combo(VK_CONTROL, VK_V)
        combo(VK_CONTROL, VK_A)
        sentinel = "ECHOLINK_CLIPBOARD_SENTINEL"
        pyperclip.copy(sentinel)
        combo(VK_CONTROL, VK_SHIFT, VK_SPACE)
        deadline = time.monotonic() + 4.0
        hud = 0
        while time.monotonic() < deadline:
            hud = titled_window("Manuscript writing HUD", starts_with=True)
            if hud and ctypes.windll.user32.IsWindowVisible(hud):
                break
            time.sleep(0.05)
        visible = bool(hud and ctypes.windll.user32.IsWindowVisible(hud))
        clipboard_safe = pyperclip.paste() == sentinel
        print(f"writing_hud_visible={visible}")
        print(f"clipboard_restored={clipboard_safe}")
        if hud:
            ctypes.windll.user32.PostMessageW(hud, 0x0010, 0, 0)
        return 0 if visible and clipboard_safe else 5
    finally:
        if handle:
            ctypes.windll.user32.PostMessageW(handle, 0x0010, 0, 0)
        note.terminate()
        try:
            note.wait(timeout=3)
        except subprocess.TimeoutExpired:
            note.kill()


if __name__ == "__main__":
    raise SystemExit(main())
