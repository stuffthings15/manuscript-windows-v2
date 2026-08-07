"""Click Replace the way the writer does, and record whether the review advances.

Every automated run so far read the card and never pressed the button, which is
why "it pulls up the first correction, then does not proceed" was never seen.
This presses Replace on each section in turn and prints the counter before and
after every press, so a review that stops moving is visible in the output
rather than inferred.

    .venv/Scripts/python.exe tools/repro_accept_advance.py
"""
from __future__ import annotations

from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT / "src", ROOT / "tools"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from animal_essay_corpus_probe import CORPUS, _essay_files  # noqa: E402
from repro_zero_of_zero import (  # noqa: E402
    click_in_document,
    foreground_word,
    log_since,
    log_size,
    open_in_word,
    press_hotkey,
)

MAX_PRESSES = 24


def hud_window(timeout: float = 20.0):
    import uiautomation as automation

    hud = automation.GetRootControl().WindowControl(
        searchDepth=3, RegexName=r"Manuscript writing HUD.*"
    )
    return hud if hud.Exists(timeout, 0.4) else None


def _descend(control, depth: int = 0):
    if depth > 9:
        return
    for child in control.GetChildren():
        yield child
        yield from _descend(child, depth + 1)


def counter_text(hud) -> str:
    for control in _descend(hud):
        try:
            name = str(control.Name or "")
            if name.startswith("Current writing suggestion position"):
                return name.split(",", 1)[-1].strip()
        except Exception:  # noqa: BLE001 - the HUD may be repainting
            continue
    return "<none>"


def card_text(hud) -> tuple[str, str]:
    original = replacement = ""
    for control in _descend(hud):
        try:
            name = str(control.Name or "")
            if name == "Suggestion original text":
                original = str(control.GetValuePattern().Value or "")
            elif name == "Suggestion replacement text":
                replacement = str(control.GetValuePattern().Value or "")
        except Exception:  # noqa: BLE001 - the HUD may be repainting
            continue
    return original, replacement


def replace_button(hud):
    for control in _descend(hud):
        try:
            if control.ControlTypeName != "ButtonControl":
                continue
            name = str(control.Name or "")
            if name.startswith("Replace text"):
                return control
        except Exception:  # noqa: BLE001 - the HUD may be repainting
            continue
    return None


def main() -> int:
    essays = _essay_files(CORPUS)
    source = essays[0]
    work = ROOT / "build" / "repro"
    work.mkdir(parents=True, exist_ok=True)
    target = work / source.name
    shutil.copy2(source, target)

    offset = log_size()
    open_in_word(target)
    _handle, bounds = foreground_word()
    click_in_document(bounds)
    press_hotkey()

    hud = hud_window(30.0)
    if hud is None:
        print("FAIL: no HUD window appeared")
        return 1

    # The card fills in only after the background proofread reaches the first
    # section, so wait for a real counter rather than reading the default.
    deadline = time.monotonic() + 90.0
    while time.monotonic() < deadline:
        if counter_text(hud) not in ("<none>", "0 of 0", ""):
            break
        time.sleep(1.0)

    seen_counters: list[str] = []
    for press in range(1, MAX_PRESSES + 1):
        hud = hud_window(10.0)
        if hud is None:
            print(f"press {press}: HUD closed")
            break
        before = counter_text(hud)
        original, replacement = card_text(hud)
        button = replace_button(hud)
        print(f"\npress {press}: counter before = {before!r}")
        print(f"  original   : {original[:110]!r}")
        print(f"  replacement: {replacement[:110]!r}")
        if button is None:
            print("  no Replace button on the card; stopping")
            break
        if not button.IsEnabled:
            print("  Replace button is disabled; waiting 10s")
            time.sleep(10.0)
            button = replace_button(hud)
            if button is None or not button.IsEnabled:
                print("  still disabled; stopping")
                break
        try:
            button.GetInvokePattern().Invoke()
        except Exception:  # noqa: BLE001 - fall back to a real click
            button.Click(simulateMove=False)
        time.sleep(6.0)

        hud = hud_window(10.0)
        if hud is None:
            print("  after press: HUD closed (review finished or crashed)")
            break
        after = counter_text(hud)
        print(f"  counter after = {after!r}")
        seen_counters.append(f"{before} -> {after}")
        if after == before:
            print("  DID NOT ADVANCE")
            time.sleep(8.0)
            again = counter_text(hud_window(10.0) or hud)
            print(f"  counter 8s later = {again!r}")
            if again == before:
                print("  STUCK: the review is not moving past this section")
                break

    print("\n--- counter transitions ---")
    for line in seen_counters:
        print(f"  {line}")

    print("\n--- app log for this attempt ---")
    print(log_since(offset).strip() or "<nothing logged>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
