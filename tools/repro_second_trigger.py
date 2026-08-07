"""Open the Writing HUD a second time in a document that is already open.

The first trigger works - that is measured. The owner's "0 of 0" report has to
come from some state the first trigger does not have, and a document that has
already been reviewed once is the cheapest candidate: a section that was
accepted or dismissed is removed from the queue, so a second look at a fully
handled document legitimately has nothing left, and the card says "0 of 0".

Read-only apart from opening the HUD.

    .venv/Scripts/python.exe tools/repro_second_trigger.py
"""
from __future__ import annotations

import ctypes
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT / "src", ROOT / "tools"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from repro_zero_of_zero import (  # noqa: E402
    click_in_document,
    foreground_word,
    log_since,
    log_size,
    press_hotkey,
)


def dump_hud() -> None:
    import uiautomation as automation

    hud = automation.GetRootControl().WindowControl(
        searchDepth=3, RegexName=r"Manuscript writing HUD.*"
    )
    if not hud.Exists(25.0, 0.5):
        print("<no HUD window on screen>")
        return

    def walk(control, depth: int = 0) -> None:
        if depth > 9:
            return
        for child in control.GetChildren():
            try:
                name = str(child.Name or "").strip()
                value = ""
                try:
                    value = str(child.GetValuePattern().Value or "").strip()
                except Exception:  # noqa: BLE001 - most controls have no value
                    value = ""
                if name or value:
                    line = f"{'  ' * depth}{child.ControlTypeName}"
                    if name:
                        line += f" name={name!r}"
                    if value:
                        line += f" value={value[:220]!r}"
                    print(line)
            except Exception:  # noqa: BLE001 - the HUD may be repainting
                pass
            walk(child, depth + 1)

    walk(hud)


def close_hud() -> None:
    import uiautomation as automation

    hud = automation.GetRootControl().WindowControl(
        searchDepth=3, RegexName=r"Manuscript writing HUD.*"
    )
    if not hud.Exists(5.0, 0.3):
        return
    for control in hud.GetChildren():
        pass
    # Escape is the documented way a writer closes the card.
    hud.SetFocus()
    time.sleep(0.3)
    ctypes.windll.user32.keybd_event(0x1B, 0, 0, 0)
    time.sleep(0.05)
    ctypes.windll.user32.keybd_event(0x1B, 0, 2, 0)
    time.sleep(1.5)


def main() -> int:
    print("=== closing any open HUD ===")
    close_hud()

    offset = log_size()
    print("=== second trigger in the same document ===")
    _handle, bounds = foreground_word()
    point = click_in_document(bounds)
    print(f"clicked at {point}")
    press_hotkey()
    time.sleep(30.0)

    print("\n--- HUD contents ---")
    dump_hud()

    print("\n--- app log for this attempt ---")
    print(log_since(offset).strip() or "<nothing logged>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
