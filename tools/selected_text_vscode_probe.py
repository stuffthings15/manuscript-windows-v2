from __future__ import annotations

import ctypes
import shutil
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

import pyperclip


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from echo_link_core.insertion import WindowsTextInserter, _send_combo, _send_key


CODE = Path.home() / "AppData/Local/Programs/Microsoft VS Code/Code.exe"
FILENAME = "echolink_selection_probe.txt"
SOURCE = "Alpha beta gamma delta"
SELECTED = "beta gamma"
REPLACEMENT = "BETA GAMMA"
EXPECTED = "Alpha BETA GAMMA delta"
VK_CONTROL = 0x11
VK_A = 0x41
VK_C = 0x43
VK_F = 0x46
VK_V = 0x56
VK_ESCAPE = 0x1B


def titled_window(fragment: str) -> int:
    found: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(handle, _extra):
        length = ctypes.windll.user32.GetWindowTextLengthW(handle)
        title = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(handle, title, length + 1)
        if ctypes.windll.user32.IsWindowVisible(handle) and fragment.lower() in title.value.lower():
            found.append(int(handle))
            return False
        return True

    ctypes.windll.user32.EnumWindows(callback_type(visit), 0)
    return found[0] if found else 0


def force_foreground(handle: int) -> bool:
    user32 = ctypes.windll.user32
    foreground = int(user32.GetForegroundWindow() or 0)
    current_thread = int(ctypes.windll.kernel32.GetCurrentThreadId())
    target_thread = int(user32.GetWindowThreadProcessId(handle, None))
    foreground_thread = int(user32.GetWindowThreadProcessId(foreground, None)) if foreground else 0
    attached_target = bool(target_thread and target_thread != current_thread and user32.AttachThreadInput(current_thread, target_thread, True))
    attached_foreground = bool(
        foreground_thread
        and foreground_thread not in (current_thread, target_thread)
        and user32.AttachThreadInput(current_thread, foreground_thread, True)
    )
    try:
        user32.ShowWindow(handle, 9)
        user32.BringWindowToTop(handle)
        user32.SetForegroundWindow(handle)
    finally:
        if attached_foreground:
            user32.AttachThreadInput(current_thread, foreground_thread, False)
        if attached_target:
            user32.AttachThreadInput(current_thread, target_thread, False)
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if int(user32.GetForegroundWindow() or 0) == handle:
            return True
        time.sleep(0.02)
    return False


def wait_for_clipboard(previous: str, timeout: float = 1.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = pyperclip.paste()
        if value != previous:
            return value
        time.sleep(0.02)
    return pyperclip.paste()


def main() -> int:
    if sys.platform != "win32" or not CODE.exists():
        print("VS Code probe requires Windows and Visual Studio Code.")
        return 2
    temp_path = Path(tempfile.mkdtemp(prefix="echolink-vscode-probe-"))
    document = temp_path / FILENAME
    user_data = temp_path / "user-data"
    extensions = temp_path / "extensions"
    document.write_text(SOURCE, encoding="utf-8")
    process = subprocess.Popen(
        [
            str(CODE),
            f"--user-data-dir={user_data}",
            f"--extensions-dir={extensions}",
            "--disable-extensions",
            "--disable-workspace-trust",
            "--skip-welcome",
            "--new-window",
            str(document),
        ]
    )
    handle = 0
    note = None
    try:
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline and not handle:
            handle = titled_window(FILENAME)
            time.sleep(0.05)
        if not handle or not force_foreground(handle):
            print("VS Code probe window could not take focus.")
            return 3
        time.sleep(1.0)
        _send_combo((VK_CONTROL, VK_F))
        time.sleep(0.2)
        pyperclip.copy(SELECTED)
        _send_combo((VK_CONTROL, VK_A))
        _send_combo((VK_CONTROL, VK_V))
        time.sleep(0.3)
        _send_key(VK_ESCAPE, up=False)
        _send_key(VK_ESCAPE, up=True)
        time.sleep(0.2)

        sentinel = "ECHOLINK_VSCODE_CLIPBOARD_SENTINEL"
        pyperclip.copy(sentinel)
        inserter = WindowsTextInserter()
        captured = inserter.capture_selection()
        clipboard_safe = pyperclip.paste() == sentinel

        note = subprocess.Popen(["notepad.exe"])
        deadline = time.monotonic() + 5.0
        note_handle = 0
        while time.monotonic() < deadline and not note_handle:
            note_handle = titled_window("Notepad")
            time.sleep(0.05)
        if not note_handle:
            print("Focus-shift window not found.")
            return 4
        force_foreground(note_handle)
        time.sleep(0.2)
        replaced = inserter.replace_selection(captured, REPLACEMENT)
        time.sleep(0.35)

        force_foreground(handle)
        time.sleep(0.2)
        pyperclip.copy(sentinel)
        _send_combo((VK_CONTROL, VK_A))
        _send_combo((VK_CONTROL, VK_C))
        actual = wait_for_clipboard(sentinel).strip()
        passed = replaced is not None and captured.text == SELECTED and actual == EXPECTED and clipboard_safe
        print(
            f"vscode: passed={passed} captured={captured.text!r} "
            f"text={actual!r} clipboard_restored={clipboard_safe}"
        )
        return 0 if passed else 1
    finally:
        if note is not None:
            note.terminate()
        if handle:
            ctypes.windll.user32.PostMessageW(handle, 0x0010, 0, 0)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                shutil.rmtree(temp_path)
                break
            except OSError:
                time.sleep(0.1)


if __name__ == "__main__":
    raise SystemExit(main())
