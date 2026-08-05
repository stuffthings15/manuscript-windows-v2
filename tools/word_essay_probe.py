"""Work through the essay open in Word exactly as a person would.

The corpus probe drives Notepad, which is not where this app is actually
used. This one leaves the document where the writer has it - open in
Microsoft Word - selects the whole story so the Writing HUD appears, then
accepts each review section in turn and reports what changed.

Read-only except for the corrections the HUD itself applies.

    .venv/Scripts/python.exe tools/word_essay_probe.py
    .venv/Scripts/python.exe tools/word_essay_probe.py --open <path.docx>
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT / "src", ROOT / "tools"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from full_page_user_journey_probe import (  # noqa: E402
    _badge_button,
    _click,
    _counter_text,
    _hud_window,
)
from writing_hud_apply_probe import (  # noqa: E402
    _accept_button,
    _descendants,
    _dismiss_existing_hud,
)
from echo_link_core.insertion import (  # noqa: E402
    _active_word_application,
    _word_visible_text_and_spans,
)

HUD_PREFIX = "Manuscript writing HUD"


def _word_application(attempts: int = 6):
    """Return Word, starting it if it is not already running.

    ``GetActiveObject`` only attaches to a running instance, so the probe
    could not run at all after Word was closed - and it also raises "Call was
    rejected by callee" while Word is busy opening or shutting down, which a
    fresh run hits constantly. Start one when there is none, and retry the
    busy case instead of dying on it.
    """
    import comtypes.client

    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return _active_word_application()
        except OSError as exc:  # no running instance, or Word is busy
            last_error = exc
        except Exception as exc:  # noqa: BLE001 - COMError is not an OSError
            last_error = exc
        try:
            word = comtypes.client.CreateObject("Word.Application", dynamic=True)
            word.Visible = True
            return word
        except Exception as exc:  # noqa: BLE001 - Word may still be starting
            last_error = exc
        time.sleep(1.0 + attempt)
    raise SystemExit(f"Could not reach Microsoft Word: {last_error}")


def word_document(path: Path | None):
    """Return the live Word application and its active document."""
    word = _word_application()
    if path is not None:
        resolved = str(path.resolve())
        for attempt in range(5):
            try:
                word.Documents.Open(resolved)
                break
            except Exception:  # noqa: BLE001 - Word rejects calls while busy
                if attempt == 4:
                    raise
                time.sleep(1.5 + attempt)
        time.sleep(2.0)
    return word, word.ActiveDocument


def visible_text(document) -> str:
    text, _spans = _word_visible_text_and_spans(str(document.Content.Text or ""))
    return text


def focus_word(word) -> int:
    """Bring the Word window to the foreground and return its handle.

    ``Application.Activate`` alone does not move focus when the caller is a
    background process, and Windows then hands the hotkey to whatever really
    is in front. The app looked at the wrong window and reported that no
    text was selected, on a document where everything was selected.
    """
    try:
        word.Visible = True
        word.Activate()
    except Exception:  # noqa: BLE001 - Word raises assorted COM errors here
        pass
    import uiautomation as automation

    handle = 0
    window = automation.GetRootControl().WindowControl(
        searchDepth=2,
        RegexName=r".*- Word$",
    )
    if window.Exists(5.0, 0.3):
        handle = int(window.NativeWindowHandle or 0)
    if handle:
        user32 = ctypes.windll.user32
        user32.ShowWindow(handle, 9)  # SW_RESTORE
        # Windows refuses a foreground change from a background process
        # unless the calling thread is attached to the target's input queue.
        current = user32.GetWindowThreadProcessId(
            user32.GetForegroundWindow(), None
        )
        target = user32.GetWindowThreadProcessId(handle, None)
        user32.AttachThreadInput(current, target, True)
        user32.BringWindowToTop(handle)
        user32.SetForegroundWindow(handle)
        user32.AttachThreadInput(current, target, False)
    time.sleep(1.0)
    return handle


def select_whole_story(word) -> None:
    """Select the entire document, which is what makes the badge appear."""
    word.Selection.WholeStory()
    time.sleep(0.8)


_VK = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12, "space": 0x20}


def press_hud_hotkey(combination: str) -> None:
    """Press the Writing HUD hotkey, the way the writer would.

    A selection made through COM does not always raise the accessibility
    event the automatic badge listens for, so the documented manual shortcut
    is the reliable way to ask for a review of what is selected.
    """
    user32 = ctypes.windll.user32
    keys = [_VK[part] for part in combination.split("+") if part in _VK]
    for key in keys:
        user32.keybd_event(key, 0, 0, 0)
        time.sleep(0.04)
    for key in reversed(keys):
        user32.keybd_event(key, 0, 2, 0)
        time.sleep(0.04)


def skip_button(hud):
    for control in _descendants(hud):
        try:
            name = str(control.Name).strip().casefold()
            if control.ControlTypeName == "ButtonControl" and (
                name == "skip"
                or name.startswith("dismiss current sentence")
                or name.startswith("dismiss suggested text")
            ):
                return control
        except Exception:  # noqa: BLE001 - UI Automation is best effort
            continue
    return None


def run(
    *,
    open_path: Path | None,
    first_wait: float,
    section_wait: float,
    hotkey: str = "ctrl+shift+space",
) -> dict[str, object]:
    _dismiss_existing_hud()
    word, document = word_document(open_path)
    name = str(document.Name or "")
    before = visible_text(document)
    result: dict[str, object] = {
        "document": name,
        "characters_before": len(before),
        "text_before": before,
        "sections": [],
    }
    started = time.monotonic()

    focus_word(word)
    select_whole_story(word)

    hud = _hud_window(8.0)
    if hud is None:
        press_hud_hotkey(hotkey)
        hud = _hud_window(20.0)
    if hud is None:
        result["error"] = (
            "the Writing HUD never appeared for the selection, with or "
            f"without {hotkey}"
        )
        result["text_after"] = before
        return result
    badge = _badge_button(hud)
    if badge is not None:
        result["badge_name"] = str(badge.Name)
        _click(badge)

    seen: dict[str, int] = {}
    while True:
        wait_limit = first_wait if not result["sections"] else section_wait
        wait_started = time.monotonic()
        deadline = wait_started + wait_limit
        accept = None
        skip = None
        counter = ""
        hud = _hud_window(3.0)
        while time.monotonic() < deadline:
            if hud is None:
                hud = _hud_window(1.0)
                if hud is None:
                    break
            try:
                counter = _counter_text(hud) or counter
                accept = _accept_button(hud)
                skip = skip_button(hud)
            except Exception:  # noqa: BLE001 - the HUD may be repainting
                hud = None
                continue
            if accept is not None and accept.IsEnabled:
                break
            accept = None
            time.sleep(0.25)
        waited = round(time.monotonic() - wait_started, 3)
        if hud is None:
            break

        if accept is None:
            if skip is None or not _click(skip):
                result["error"] = (
                    f"section {counter or '?'} offered neither a replacement "
                    f"nor a skip within {wait_limit}s"
                )
                break
            result["sections"].append(
                {"counter": counter, "wait_seconds": waited, "accepted": False}
            )
        else:
            if not _click(accept):
                result["error"] = f"accept click failed at {counter or '?'}"
                break
            time.sleep(1.2)
            result["sections"].append(
                {"counter": counter, "wait_seconds": waited, "accepted": True}
            )

        if counter:
            seen[counter] = seen.get(counter, 0) + 1
            if seen[counter] > 3:
                result["error"] = f"section counter stuck at {counter}"
                break
        if len(result["sections"]) > 200:
            result["error"] = "exceeded 200 review decisions"
            break
        time.sleep(0.6)
        if _hud_window(2.5) is None:
            break

    after = visible_text(document)
    result["text_after"] = after
    result["characters_after"] = len(after)
    result["changed"] = after != before
    result["accepted_sections"] = sum(
        1 for value in result["sections"] if value.get("accepted")
    )
    result["skipped_sections"] = sum(
        1 for value in result["sections"] if not value.get("accepted")
    )
    result["total_seconds"] = round(time.monotonic() - started, 3)
    _dismiss_existing_hud()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Review the essay open in Word through the Writing HUD",
    )
    parser.add_argument("--open", type=Path, default=None)
    parser.add_argument("--first-wait", type=float, default=150.0)
    parser.add_argument("--section-wait", type=float, default=120.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "build" / "word-essay-run.json",
    )
    parser.add_argument("--show", type=int, default=6)
    parser.add_argument("--hotkey", default="ctrl+shift+space")
    args = parser.parse_args(argv)

    report = run(
        open_path=args.open,
        first_wait=args.first_wait,
        section_wait=args.section_wait,
        hotkey=args.hotkey,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "probe": "word_essay",
                "generated_at": datetime.now(timezone.utc).isoformat(
                    timespec="seconds"
                ),
                **report,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        f"document={report.get('document')} "
        f"sections={len(report.get('sections') or [])} "
        f"accepted={report.get('accepted_sections')} "
        f"skipped={report.get('skipped_sections')} "
        f"changed={report.get('changed')} "
        f"total={report.get('total_seconds')}s "
        f"error={report.get('error', '')}",
        flush=True,
    )

    before = str(report.get("text_before") or "").split("\n")
    after = str(report.get("text_after") or "").split("\n")
    shown = 0
    for index, (left, right) in enumerate(zip(before, after), start=1):
        if left.strip() == right.strip() or shown >= args.show:
            continue
        shown += 1
        print(f"\n--- paragraph {index} ---")
        print(f"WAS: {left[:240]}")
        print(f"NOW: {right[:240]}")
    print(f"\n{args.output}")
    return 0 if report.get("changed") and not report.get("error") else 5


if __name__ == "__main__":
    raise SystemExit(main())
