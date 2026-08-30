"""Reproduce the owner's report: click in a Word essay, open the HUD, read "0 of 0".

Deliberately minimal. The full session harness has its own failure modes and
seven of its runs failed in the harness rather than the app, so this does only
the three things the owner does - open the document, click in it, press the
shortcut - and then prints exactly what the app logged for that one attempt.

    .venv/Scripts/python.exe tools/repro_zero_of_zero.py
"""
from __future__ import annotations

import ctypes
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT / "src", ROOT / "tools"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from animal_essay_corpus_probe import CORPUS, _essay_files  # noqa: E402

LOG = Path.home() / "AppData/Local/ManuscriptWindowsV2/echo-link.log"
_VK = {"ctrl": 0x11, "shift": 0x10, "space": 0x20}


def log_size() -> int:
    return LOG.stat().st_size if LOG.exists() else 0


def log_since(offset: int) -> str:
    if not LOG.exists():
        return ""
    with LOG.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(offset)
        return handle.read()


def open_in_word(path: Path):
    import comtypes.client

    word = comtypes.client.CreateObject("Word.Application", dynamic=True)
    word.Visible = True
    word.Documents.Open(str(path.resolve()))
    time.sleep(3.0)
    return word


def _word_handle(timeout: float = 15.0) -> int:
    """The Word window's handle, by Win32 enumeration rather than a UIA walk.

    The same hazard `repro_accept_advance.hud_window` carried, in the other
    function this batch imports. `GetRootControl().WindowControl(...).Exists()`
    re-walks every top-level window and its children until it matches or times
    out, and one slow or dying window anywhere on the desktop raises
    `COMError (-2146233083)` from `GetNextSiblingControl` and ends the run.

    Found by fixing the HUD lookup and then grepping for the shape rather than
    waiting for it to cost another five essays. `hud_counter` below has it too
    and is deliberately left alone: nothing in the batch path calls it, and
    changing code that no measurement covers is how untested churn gets in.
    """
    user32 = ctypes.windll.user32
    proto = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)
    )
    deadline = time.monotonic() + timeout
    while True:
        found = ctypes.c_int(0)

        def callback(hwnd, result):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            if buffer.value.endswith("- Word"):
                result[0] = hwnd
                return False
            return True

        user32.EnumWindows(proto(callback), ctypes.byref(found))
        if found.value:
            return int(found.value)
        if time.monotonic() >= deadline:
            return 0
        time.sleep(0.2)


def foreground_word() -> tuple[int, tuple[int, int, int, int]]:
    import uiautomation as automation

    handle = _word_handle()
    if not handle:
        raise SystemExit("no Word window found")
    user32 = ctypes.windll.user32
    user32.ShowWindow(handle, 9)
    current = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    target = user32.GetWindowThreadProcessId(handle, None)
    user32.AttachThreadInput(current, target, True)
    user32.BringWindowToTop(handle)
    user32.SetForegroundWindow(handle)
    user32.AttachThreadInput(current, target, False)
    time.sleep(1.5)
    # One UIA call, on a window already known to exist, instead of a search.
    window = automation.ControlFromHandle(handle)
    rect = window.BoundingRectangle
    return handle, (rect.left, rect.top, rect.right, rect.bottom)


def click_in_document(bounds: tuple[int, int, int, int]) -> tuple[int, int]:
    """Click where the writer's text is, not on the ribbon or the margin."""
    left, top, right, bottom = bounds
    x = (left + right) // 2
    # Below the ribbon, well inside the first page of body text.
    y = top + int((bottom - top) * 0.40)
    user32 = ctypes.windll.user32
    user32.SetCursorPos(x, y)
    time.sleep(0.4)
    user32.mouse_event(0x0002, 0, 0, 0, 0)  # left down
    time.sleep(0.08)
    user32.mouse_event(0x0004, 0, 0, 0, 0)  # left up
    time.sleep(1.2)
    return x, y


def press_hotkey() -> None:
    user32 = ctypes.windll.user32
    keys = [_VK["ctrl"], _VK["shift"], _VK["space"]]
    for key in keys:
        user32.keybd_event(key, 0, 0, 0)
        time.sleep(0.05)
    for key in reversed(keys):
        user32.keybd_event(key, 0, 2, 0)
        time.sleep(0.05)


def hud_counter() -> str:
    # Found by handle, like every other window lookup in the batch path.
    #
    # I argued for leaving this one alone because nothing in the batch calls it,
    # and tests/test_harness_window_lookup.py made the better case: it lives in
    # a module run_essays_batch imports, so it is one call away from costing
    # another five essays, and "unused today" is not a property anyone maintains.
    from full_page_user_journey_probe import _hud_window

    hud = _hud_window(25.0)
    if hud is None:
        return "<no HUD window>"
    found: list[str] = []

    def walk(control, depth: int = 0) -> None:
        if depth > 8:
            return
        for child in control.GetChildren():
            try:
                name = str(child.Name or "").strip()
                if name:
                    found.append(f"{child.ControlTypeName}: {name}")
            except Exception:  # noqa: BLE001 - the HUD may be repainting
                pass
            walk(child, depth + 1)

    walk(hud)
    return "\n".join(found)


def main() -> int:
    essays = _essay_files(CORPUS)
    if not essays:
        raise SystemExit(f"no essays under {CORPUS}")
    source = essays[0]
    work = ROOT / "build" / "repro"
    work.mkdir(parents=True, exist_ok=True)
    target = work / source.name
    shutil.copy2(source, target)
    print(f"essay: {target}")

    offset = log_size()
    open_in_word(target)
    _handle, bounds = foreground_word()
    point = click_in_document(bounds)
    print(f"clicked at {point} inside {bounds}")
    press_hotkey()

    print("\n--- HUD contents ---")
    print(hud_counter())

    time.sleep(20.0)
    print("\n--- app log for this attempt ---")
    print(log_since(offset).strip() or "<nothing logged>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
