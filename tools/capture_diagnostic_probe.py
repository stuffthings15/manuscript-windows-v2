"""Diagnose why document capture degrades to selection scope on this desktop.

Opens Notepad with a known multi-paragraph page, focuses it, and reports each
step of the capture pipeline with content-free details: focused control type,
window provider result, candidate walk, text-pattern availability, document
range length, and the reader outcomes. Runs only when the desktop is idle.
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import uiautomation as automation  # noqa: E402

from echo_link_core import windows_selection as ws  # noqa: E402


class _LastInputInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def _idle_seconds() -> float:
    info = _LastInputInfo()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    return max(0.0, (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000.0)


def main() -> int:
    waited = 0.0
    while _idle_seconds() < 60.0:
        if waited > 30 * 60:
            print("desktop never idle")
            return 3
        time.sleep(10)
        waited += 10

    from full_page_user_journey_probe import _DENSE_PAGE_PARAGRAPHS
    text = chr(10).join(_DENSE_PAGE_PARAGRAPHS)
    root = Path(tempfile.mkdtemp(prefix="capture-diag-"))
    page = root / f"manuscript-diag-{time.time_ns()}.txt"
    page.write_text(text, encoding="utf-8")
    process = subprocess.Popen(["notepad.exe", str(page)])
    handle = 0
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not handle:
        for control in automation.GetRootControl().GetChildren():
            try:
                if (
                    control.ControlTypeName == "WindowControl"
                    and page.stem in control.Name
                    and ctypes.windll.user32.IsWindowVisible(
                        control.NativeWindowHandle
                    )
                ):
                    handle = control.NativeWindowHandle
                    break
            except Exception:
                continue
        time.sleep(0.2)
    print("notepad handle:", handle)
    ctypes.windll.user32.SetForegroundWindow(handle)
    time.sleep(0.8)
    fg = ctypes.windll.user32.GetForegroundWindow()
    print("foreground==notepad:", fg == handle, "| fg:", fg)

    reader = ws.WindowsSelectionReader(
        max_characters=500_000, include_caret_sentence=True
    )
    provider_window = reader._window_provider()
    print(
        "window_provider handle:", provider_window.handle,
        "| pid:", provider_window.process_id,
        "| title set:", bool(provider_window.title),
    )
    try:
        focused = reader._automation().GetFocusedControl()
        print(
            "focused control:", focused.ControlTypeName,
            "| class:", getattr(focused, "ClassName", ""),
        )
        candidates = list(
            ws._candidate_text_controls(
                focused,
                reader._automation(),
                provider_window,
                own_process_id=reader._own_process_id,
            )
        )
        print("candidates:", [
            (c.ControlTypeName, getattr(c, "ClassName", "")) for c in candidates
        ])
        for control in candidates:
            pattern = ws._text_pattern(control, reader._automation())
            print(
                "  candidate pattern:", pattern is not None,
                "| editable:", ws._is_editable_text_control(
                    control, reader._automation()
                ),
            )
            if pattern is not None:
                document_range = getattr(pattern, "DocumentRange", None)
                if document_range is not None:
                    doc_text = document_range.GetText(500_001)
                    print(
                        "  document range chars:",
                        len(doc_text) if isinstance(doc_text, str) else None,
                    )
                    print(
                        "  document anchor:",
                        ws._selection_anchor(document_range),
                    )
                selection = tuple(pattern.GetSelection() or ())
                print("  selection ranges:", len(selection))
    except Exception as exc:
        print("focused walk failed:", type(exc).__name__, str(exc)[:120])

    original_anchor = ws._selection_anchor
    def anchor_spy(value):
        result = original_anchor(value)
        if result is None:
            print("  _selection_anchor -> None")
        return result
    ws._selection_anchor = anchor_spy
    original_pattern = ws._text_pattern
    def pattern_spy(control, auto):
        result = original_pattern(control, auto)
        if result is None:
            print("  _text_pattern -> None for", control.ControlTypeName)
        return result
    ws._text_pattern = pattern_spy

    detected = reader.read()
    print(
        "read():",
        None
        if detected is None
        else (detected.record.scope, len(detected.record.text)),
    )
    document = reader.read_document()
    print(
        "read_document():",
        None
        if document is None
        else (document.record.scope, len(document.record.text)),
    )
    ctypes.windll.user32.PostMessageW(handle, 0x0010, 0, 0)
    time.sleep(0.5)
    if process.poll() is None:
        process.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
