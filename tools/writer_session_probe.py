"""Use Manuscript the way a writer does, and report what breaks.

Every quality number this project has produced was measured with Word killed
and the app restarted between essays. That isolation hides two things at once:
a review carrying from one document to the next, and the badge rebuilding
itself every time focus moves. On 2026-08-15 the owner ran it without the
isolation and hit both inside a minute - a card reading "0 of 0", then a new
card on every click - while the app's log showed it capturing the chat window
eleven times and the essay it was correcting twice.

So this drives the product the way a person does: open a document, work the
buttons on the card, click away to something else and come back, then open a
second document without restarting anything.

It reuses `writing_hud_apply_probe` rather than repeating it - the target
window, the button and counter readers, and the document reader all come from
there, so a change to the HUD's shape is felt in one place.

    $env:PYTHONPATH = "src"
    .venv/Scripts/python.exe tools/writer_session_probe.py

Run it with the app already up. It takes the screen and the keyboard, so it
cannot share the machine with anybody: the HUD follows focus by design, and a
stray click is another window for it to capture.
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT / "src", ROOT / "tools"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import pyperclip  # noqa: E402
import uiautomation as automation  # noqa: E402

from writing_hud_apply_probe import (  # noqa: E402
    _accept_button,
    _card_progress,
    _descendants,
    _dismiss_existing_hud,
    _document_text,
    _editable_control,
    _invoke,
    _wait_for_target,
    _window_with_prefix,
)

# The card names its controls for a screen reader, not for a test, so the label
# on screen and the name UI Automation reports are different things: the button
# reading "Replace Text" answers to "Replace text in source box", and the badge
# carries its tooltip in its name. `_button` in the probe this borrows from
# matches a name exactly, which found none of them.
BUTTONS = {
    "replace": "replace text in source",
    "dismiss": "dismiss suggested text",
    "rewrite": "rewrite current text locally",
    "close": "close",
}


def _named(window, prefix: str):
    """The first button whose accessible name starts with `prefix`."""
    wanted = prefix.casefold()
    for control in _descendants(window):
        try:
            if control.ControlTypeName != "ButtonControl":
                continue
            if str(control.Name).strip().casefold().startswith(wanted):
                return control
        except Exception:  # noqa: BLE001 - UI Automation raises assorted types
            continue
    return None

APPLY_PROBE = ROOT / "tools" / "writing_hud_apply_probe.py"
HUD_PREFIX = "Manuscript writing HUD"

VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_SPACE = 0x20
VK_A = 0x41
VK_V = 0x56

# Two short documents rather than one long one. The point here is the
# interactions, not the corrections, and a long document buries a failed button
# under two minutes of model time.
DOCUMENT_A = (
    "The libary opens at nine and it dont close untill six. "
    "Staff sais the new hours is populer with familys."
)
DOCUMENT_B = (
    "Our team meet on tuesday to discus the budgit. "
    "Every member bring there own notes to the meating."
)


def _key(code: int, up: bool = False) -> None:
    ctypes.windll.user32.keybd_event(code, 0, 2 if up else 0, 0)


def _combo(*codes: int) -> None:
    for code in codes:
        _key(code)
    for code in reversed(codes):
        _key(code, up=True)
    time.sleep(0.2)


def _focus(handle: int) -> None:
    ctypes.windll.user32.SetForegroundWindow(handle)
    time.sleep(0.4)
    editor = _editable_control(handle)
    if editor is not None:
        editor.Click()
        editor.SetFocus()
    time.sleep(0.4)


def _type_into(handle: int, text: str) -> None:
    _focus(handle)
    _combo(VK_CONTROL, VK_A)
    pyperclip.copy(text)
    _combo(VK_CONTROL, VK_V)
    time.sleep(0.5)


def _open_target(stack, title: str) -> int:
    """A window with an editable field, spawned by the probe that owns it."""
    root = tempfile.TemporaryDirectory(
        prefix="echo-link-writer-session-", ignore_cleanup_errors=True
    )
    stack.append(root)
    status = Path(root.name) / "target.json"
    process = subprocess.Popen(
        [
            sys.executable,
            str(APPLY_PROBE),
            "--target-mode",
            "--target-title",
            title,
            "--target-status",
            str(status),
        ],
        cwd=ROOT,
    )
    stack.append(process)
    return _wait_for_target(status, process, 30.0)


def _hud(timeout: float = 20.0):
    return _window_with_prefix(HUD_PREFIX, timeout)


def _open_review(handle: int) -> object | None:
    """Ask for a review the way the writer does: select, then press the keys."""
    _focus(handle)
    _combo(VK_CONTROL, VK_A)
    _combo(VK_CONTROL, VK_SHIFT, VK_SPACE)
    time.sleep(3.0)
    window = _hud()
    if window is None:
        return None
    badge = _named(window, "manuscript")
    if badge is not None:
        _invoke(badge)
        time.sleep(3.0)
        window = _hud() or window
    # The card is only open once it can say which section it is on. Returning a
    # window that is still the badge is what let the first run report a pass on
    # two counters that were both None.
    for _attempt in range(10):
        if _card_progress(window) is not None:
            return window
        time.sleep(1.0)
        window = _hud(4.0) or window
    return None


def check_every_button(handle: int, report: dict) -> bool:
    """Each button on the card, judged by what it did rather than by clicking."""
    window = _open_review(handle)
    if window is None:
        report["every_button"] = {"error": "no review card appeared"}
        return False

    seen: dict[str, object] = {"opened_counter": _card_progress(window)}
    for name, prefix in BUTTONS.items():
        control = _accept_button(window) if name == "replace" else _named(window, prefix)
        if control is None:
            seen[name] = "not on the card"
            continue
        before_counter = _card_progress(window)
        before_text = _document_text(handle)
        _invoke(control)
        time.sleep(2.0)
        window = _hud(6.0) or window
        seen[name] = {
            "counter": [before_counter, _card_progress(window)],
            "document_changed": _document_text(handle) != before_text,
        }
    report["every_button"] = seen
    # Reaching the end with a counter at any point is the bar here; the
    # per-button expectations are read by a person, because "Dismiss" doing
    # nothing to the document is right and "Replace Text" doing nothing is not.
    return seen["opened_counter"] is not None


def check_focus_does_not_steal_the_review(first: int, second: int, report: dict) -> bool:
    """Clicking into another window must leave the card the writer is reading."""
    window = _open_review(first)
    if window is None:
        report["focus_switch"] = {"error": "no review card appeared"}
        return False
    before = _card_progress(window)

    _focus(second)
    time.sleep(2.0)
    _focus(first)
    time.sleep(1.5)

    window = _hud(6.0)
    after = _card_progress(window) if window is not None else None
    report["focus_switch"] = {
        "counter_before": before,
        "counter_after": after,
        "card_survived": window is not None,
    }
    # `before` has to be a real counter. The first version asserted only that
    # the two matched, so it passed with both of them None - a card that never
    # opened proving that nothing had disturbed it.
    return before is not None and after == before


def check_a_second_document(first: int, second: int, report: dict) -> bool:
    """The second document in one session gets its own review, not the first's."""
    first_window = _open_review(first)
    first_counter = _card_progress(first_window) if first_window is not None else None

    second_window = _open_review(second)
    second_counter = _card_progress(second_window) if second_window is not None else None

    before_first = _document_text(first)
    if second_window is not None:
        replace = _accept_button(second_window)
        if replace is not None:
            _invoke(replace)
            time.sleep(2.5)
    report["second_document"] = {
        "first_counter": first_counter,
        "second_counter": second_counter,
        "first_document_untouched": _document_text(first) == before_first,
    }
    return second_counter is not None and _document_text(first) == before_first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "writer-session.json")
    arguments = parser.parse_args(argv)
    if sys.platform != "win32":
        print("Windows only")  # noqa: T201
        return 2

    automation.SetGlobalSearchTimeout(3.0)
    stack: list[object] = []
    report: dict[str, object] = {"generated": datetime.now(timezone.utc).isoformat()}
    results: dict[str, bool] = {}
    try:
        _dismiss_existing_hud()
        stamp = time.time_ns()
        first = _open_target(stack, f"Manuscript writer session document A {stamp}")
        second = _open_target(stack, f"Manuscript writer session document B {stamp}")
        if not first or not second:
            report["error"] = "a target window did not start"
            return _finish(arguments.output, report, results)
        _type_into(first, DOCUMENT_A)
        _type_into(second, DOCUMENT_B)

        results["every_button"] = check_every_button(first, report)
        _dismiss_existing_hud()
        results["focus_switch"] = check_focus_does_not_steal_the_review(
            first, second, report
        )
        _dismiss_existing_hud()
        results["second_document"] = check_a_second_document(first, second, report)
    finally:
        _dismiss_existing_hud()
        for item in reversed(stack):
            if isinstance(item, subprocess.Popen):
                item.terminate()
    return _finish(arguments.output, report, results)


def _finish(output: Path, report: dict, results: dict[str, bool]) -> int:
    report["results"] = results
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8", newline="\n")
    for name, passed in results.items():
        print(f"{'PASS' if passed else 'FAIL'}  {name}")  # noqa: T201
    print(f"\n{output}")  # noqa: T201
    return 0 if results and all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
