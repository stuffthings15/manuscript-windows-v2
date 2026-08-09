"""Walk every tab of the running app and report what a user can actually reach.

Static analysis found five controls in the Writing HUD that are created but
never shown. That class of defect - a feature that exists in code and not on
screen - is invisible to the test suite, so this drives the real window:
selects each tab, enumerates its controls, and reports anything disabled,
unnamed, or unreachable.

    .venv/Scripts/python.exe tools/ui_smoke_probe.py
"""
from __future__ import annotations

import sys
import time

import uiautomation as auto

INTERESTING = {
    "ButtonControl", "CheckBoxControl", "RadioButtonControl", "ComboBoxControl",
    "EditControl", "ListControl", "ListItemControl", "TabItemControl",
    "SliderControl", "TreeControl",
}


def descend(node, depth=0, limit=2500):
    stack, seen = [(node, 0)], 0
    while stack and seen < limit:
        item, level = stack.pop()
        seen += 1
        yield item, level
        try:
            for child in reversed(item.GetChildren()):
                stack.append((child, level + 1))
        except Exception:  # noqa: BLE001 - a repainting control is not fatal
            continue


def main() -> int:
    window = auto.WindowControl(searchDepth=2, RegexName=".*Manuscript.*")
    if not window.Exists(6, 1):
        print("FAIL: the Manuscript window is not open. Start the app first.")
        return 1
    print(f"window: {window.Name!r}\n")

    # The main tabs are exposed as ListItemControl, not TabItemControl. A probe
    # that looked only for tab items found three inner sub-tabs, reported "no
    # findings", and never opened Drafts, Documents, Vocabulary, Snippets,
    # Recovery, History or Sources at all.
    wanted = {
        "Drafts", "Documents", "Vocabulary", "Snippets",
        "Recovery", "History", "Sources",
    }
    tabs = [
        c for c, _ in descend(window)
        if c.ControlTypeName in {"TabItemControl", "ListItemControl"}
        and str(c.Name or "").strip() in wanted
    ]
    print(f"tabs found: {[t.Name for t in tabs]}\n")

    problems: list[str] = []
    for tab in tabs:
        name = tab.Name
        try:
            try:
                tab.GetSelectionItemPattern().Select()
            except Exception:  # noqa: BLE001 - list items may need a click
                tab.Click(simulateMove=False)
            time.sleep(0.9)
        except Exception as exc:  # noqa: BLE001 - an unselectable tab is a finding
            problems.append(f"{name}: cannot be selected ({type(exc).__name__})")
            continue
        controls = [
            c for c, _ in descend(window)
            if c.ControlTypeName in INTERESTING
            and c.ControlTypeName != "TabItemControl"
        ]
        disabled, unnamed, offscreen = [], 0, 0
        for control in controls:
            try:
                if not control.IsEnabled:
                    disabled.append(f"{control.ControlTypeName}:{control.Name[:40]}")
                if not str(control.Name or "").strip():
                    unnamed += 1
                rect = control.BoundingRectangle
                if rect.width() <= 0 or rect.height() <= 0:
                    offscreen += 1
            except Exception:  # noqa: BLE001 - counted as unreadable below
                offscreen += 1
        print(f"{name:<22} controls={len(controls):>3}  disabled={len(disabled):>2}  "
              f"unnamed={unnamed:>2}  zero-size={offscreen:>2}")
        if disabled:
            print(f"    disabled: {disabled[:6]}")
        if unnamed:
            problems.append(f"{name}: {unnamed} controls with no accessible name")
    print()
    if problems:
        print("FINDINGS:")
        for line in problems:
            print(f"  - {line}")
    else:
        print("no findings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
