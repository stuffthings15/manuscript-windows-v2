"""Watch the Writing HUD's section counter while accepting every section.

A user reported the counter jumping from section 1 to section 4 without ever
showing 2 and 3. The corpus probe only reports the first counter it disagrees
with, which is not enough to tell a skipped section from an unreadable label.

This opens a short document in Notepad, clicks the badge, then accepts each
section in turn and prints the counter before and after every accept, so the
progression is visible as a sequence rather than a single mismatch.

    .venv/Scripts/python.exe tools/hud_counter_trace_probe.py
"""
from __future__ import annotations

from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "src", ROOT / "tools"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from full_page_user_journey_probe import (  # noqa: E402
    _badge_button,
    _click,
    _counter_text,
    _hud_window,
    _open_notepad,
)
from writing_hud_apply_probe import (  # noqa: E402
    _accept_button,
    _descendants,
    _dismiss_existing_hud,
    _document_text,
    _reports_ai_ready,
)

import ctypes  # noqa: E402

VK_CONTROL = 0x11
VK_A = 0x41
KEYEVENTF_KEYUP = 0x0002


def _select_all(handle: int) -> None:
    """Focus the editor and press Ctrl+A, the way a user starts a capture."""
    ctypes.windll.user32.SetForegroundWindow(handle)
    time.sleep(0.4)
    ctypes.windll.user32.keybd_event(VK_CONTROL, 0, 0, 0)
    ctypes.windll.user32.keybd_event(VK_A, 0, 0, 0)
    ctypes.windll.user32.keybd_event(VK_A, 0, KEYEVENTF_KEYUP, 0)
    ctypes.windll.user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)

SAMPLE = (
    "teh dog run fast in the park evry day.\n\n"
    "she dont like the loud noises from teh street.\n\n"
    "we was going to the store when it start to rain.\n\n"
    "their are many resons why a pett needs care.\n"
)


def _labels(window) -> list[str]:
    names = []
    for control in _descendants(window):
        try:
            name = str(control.Name).strip()
        except Exception:  # noqa: BLE001 - the HUD may be repainting
            continue
        if name:
            names.append(f"{control.ControlTypeName}:{name}")
    return names


def main() -> int:
    _dismiss_existing_hud()
    process, target, handle = _open_notepad(SAMPLE)
    try:
        time.sleep(1.5)
        _select_all(handle)
        time.sleep(1.5)
        badge = _badge_button(20.0)
        if badge is None:
            print("FAIL: badge never appeared")
            return 1
        if not _click(badge):
            print("FAIL: badge click failed")
            return 1

        seen: list[str] = []
        for step in range(12):
            hud = _hud_window(10.0)
            if hud is None:
                print(f"step {step}: HUD window gone")
                break
            deadline = time.monotonic() + 150.0
            accept = None
            counter = ""
            while time.monotonic() < deadline:
                try:
                    counter = _counter_text(hud) or ""
                    accept = _accept_button(hud)
                    if (
                        accept is not None
                        and accept.IsEnabled
                        and _reports_ai_ready(hud, accept)
                    ):
                        break
                except Exception:  # noqa: BLE001
                    hud = _hud_window(5.0)
                    if hud is None:
                        break
                time.sleep(0.25)
            if hud is None or accept is None:
                print(f"step {step}: no accept button; counter={counter!r}")
                print("  labels:", _labels(hud)[:14] if hud else "none")
                break
            print(f"step {step}: counter BEFORE accept = {counter!r}")
            seen.append(counter)
            if not _click(accept):
                print(f"step {step}: accept click failed")
                break
            time.sleep(1.2)

        print("\ncounter sequence:", seen)
        print("document now:\n" + _document_text(handle))
        return 0
    finally:
        _dismiss_existing_hud()
        ctypes.windll.user32.PostMessageW(handle, 0x0010, 0, 0)
        time.sleep(0.5)
        if process.poll() is None:
            process.terminate()
        try:
            target.unlink(missing_ok=True)
            target.parent.rmdir()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
