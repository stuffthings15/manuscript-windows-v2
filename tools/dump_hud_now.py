"""Print what the Writing HUD is showing right now, without touching it.

Read-only. Used to tell a card that never arrived from a card that arrived
after the reader had already looked away.

    .venv/Scripts/python.exe tools/dump_hud_now.py
"""
from __future__ import annotations

import sys


def main() -> int:
    import uiautomation as automation

    hud = automation.GetRootControl().WindowControl(
        searchDepth=3, RegexName=r"Manuscript writing HUD.*"
    )
    if not hud.Exists(8.0, 0.4):
        print("<no HUD window on screen>")
        return 1
    print(f"HUD: {hud.Name}")

    def walk(control, depth: int = 0) -> None:
        if depth > 9:
            return
        for child in control.GetChildren():
            try:
                name = str(child.Name or "").strip()
                value = ""
                try:
                    pattern = child.GetValuePattern()
                    value = str(pattern.Value or "").strip()
                except Exception:  # noqa: BLE001 - most controls have no value
                    value = ""
                label = f"{'  ' * depth}{child.ControlTypeName}"
                if name:
                    label += f" name={name!r}"
                if value:
                    label += f" value={value[:300]!r}"
                if name or value:
                    print(label)
            except Exception:  # noqa: BLE001 - the HUD may be repainting
                pass
            walk(child, depth + 1)

    walk(hud)
    return 0


if __name__ == "__main__":
    sys.exit(main())
