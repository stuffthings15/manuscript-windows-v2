from __future__ import annotations

import ctypes
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

from echo_link_core.insertion import WindowsTextInserter, _send_combo


CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
TITLE = "Manuscript Browser Selection Probe"
SOURCE = "Alpha beta gamma delta"
SELECTED = "beta gamma"
REPLACEMENT = "BETA GAMMA"
EXPECTED = "Alpha BETA GAMMA delta"
VK_CONTROL = 0x11
VK_A = 0x41
VK_C = 0x43


def matching_window() -> int:
    found: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(handle, _extra):
        length = ctypes.windll.user32.GetWindowTextLengthW(handle)
        text = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(handle, text, length + 1)
        if ctypes.windll.user32.IsWindowVisible(handle) and TITLE in text.value:
            found.append(int(handle))
            return False
        return True

    ctypes.windll.user32.EnumWindows(callback_type(visit), 0)
    return found[0] if found else 0


def window_for_pid(pid: int) -> int:
    found: list[int] = []
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


def notepad_window() -> int:
    found: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(handle, _extra):
        length = ctypes.windll.user32.GetWindowTextLengthW(handle)
        title = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(handle, title, length + 1)
        if ctypes.windll.user32.IsWindowVisible(handle) and "Notepad" in title.value:
            found.append(int(handle))
            return False
        return True

    ctypes.windll.user32.EnumWindows(callback_type(visit), 0)
    return found[0] if found else 0


def wait_for_clipboard(previous: str, timeout: float = 1.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = pyperclip.paste()
        if value != previous:
            return value
        time.sleep(0.02)
    return pyperclip.paste()


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


def main() -> int:
    if sys.platform != "win32" or not CHROME.exists():
        print("Chrome browser probe requires Windows and Google Chrome.")
        return 2
    html = f"""<!doctype html><html><head><title>{TITLE}</title></head>
<body><div id=editor contenteditable=true style='font:24px sans-serif;padding:40px'>{SOURCE}</div>
<script>
const editor = document.getElementById('editor');
function selectTarget() {{
  editor.focus();
  const range = document.createRange();
  range.setStart(editor.firstChild, {SOURCE.index(SELECTED)});
  range.setEnd(editor.firstChild, {SOURCE.index(SELECTED) + len(SELECTED)});
  const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
}}
selectTarget();
window.addEventListener('focus', selectTarget);
let repeats = 0; const timer = setInterval(() => {{ selectTarget(); if (++repeats >= 6) clearInterval(timer); }}, 150);
</script></body></html>"""
    with tempfile.TemporaryDirectory(prefix="echolink-browser-probe-") as temp:
        temp_path = Path(temp)
        page = temp_path / "probe.html"
        profile = temp_path / "profile"
        page.write_text(html, encoding="utf-8")
        process = subprocess.Popen(
            [
                str(CHROME),
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--disable-background-mode",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--new-window",
                page.as_uri(),
            ]
        )
        handle = 0
        note = None
        try:
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and not handle:
                handle = matching_window()
                time.sleep(0.05)
            if not handle:
                print("Chrome probe window not found.")
                return 3
            user32 = ctypes.windll.user32
            if not force_foreground(handle):
                print("Chrome probe could not take foreground focus.")
                return 5
            time.sleep(1.1)
            sentinel = "ECHOLINK_BROWSER_CLIPBOARD_SENTINEL"
            pyperclip.copy(sentinel)
            inserter = WindowsTextInserter()
            captured = inserter.capture_selection()

            note = subprocess.Popen(["notepad.exe"])
            note_handle = 0
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and not note_handle:
                note_handle = window_for_pid(note.pid) or notepad_window()
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
            actual = wait_for_clipboard(sentinel)
            passed = replaced is not None and captured.text == SELECTED and actual.strip() == EXPECTED
            print(f"browser: passed={passed} captured={captured.text!r} text={actual.strip()!r}")
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
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
