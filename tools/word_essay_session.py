"""Correct one error-heavy essay in Word the way a writer would, and keep the result.

This is the end-to-end check the product actually has to pass: open the
essay in Microsoft Word, let the badge appear, work through every review
section, accept each correction, and then keep what came out so a person can
read it and say whether it is any good.

It writes three files per essay into ``build/corrected``:

    <name>.docx        the corrected document, saved from Word
    <name>.before.txt  the essay as it started
    <name>.after.txt   the essay as it ended

and a side-by-side ``<name>.review.md`` with one paragraph per row, which is
the file to hand to a reader for judgement.

Two things this refuses to do, both learned the hard way:

* It will not start when the local AI has fallen back to the deterministic
  rules. A rules-only run produces a thin, mostly unchanged essay and looks
  exactly like a broken corrector, so measuring one wastes the run and the
  reading after it. Pass --allow-rules-only to measure the floor on purpose.
* It never touches the locked corpus. The essay is copied into
  ``build/corrected`` first and Word only ever opens the copy.

    .venv/Scripts/python.exe tools/word_essay_session.py --essay 1
    .venv/Scripts/python.exe tools/word_essay_session.py --essay 1 --allow-rules-only
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT / "src", ROOT / "tools"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from animal_essay_corpus_probe import CORPUS, _essay_files  # noqa: E402
from full_page_user_journey_probe import (  # noqa: E402
    _badge_button,
    _click,
    _counter_text,
    _hud_window,
)
from word_essay_probe import (  # noqa: E402
    press_hud_hotkey,
    select_whole_story,
    skip_button,
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

OUTPUT_ROOT = ROOT / "build" / "corrected"
LOG_PATH = Path.home() / "AppData/Local/ManuscriptWindowsV2/echo-link.log"

_FOCUS_LINE = re.compile(
    r"Writing HUD editable focus detected title='(?P<title>.*?)' scope=(?P<scope>\w+)"
)


def captured_document_title() -> str | None:
    """The window the HUD most recently built its review from, per the app.

    The app states this itself on every capture, which makes it better
    evidence than anything this harness could infer: the HUD follows focus by
    design, so the window it captured is a fact only the app holds.

    Returns None when the log says nothing, because an absent log is not
    evidence that the capture was wrong.
    """
    try:
        text = LOG_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in reversed(text.splitlines()):
        found = _FOCUS_LINE.search(line)
        if found:
            return found.group("title")
    return None


def local_ai_state(window_seconds: float = 900.0) -> tuple[bool, str]:
    """Whether the running app has the model loaded, read from its own log.

    The app says this plainly and the answer decides whether a run is worth
    starting, so it is read rather than assumed.

    Only lines from the currently running app count. The log outlives restarts,
    and scanning back past one reported `loaded=True` from a previous instance
    while the running app had come up rules-only - so a run that corrected
    nothing was started, and then investigated as a capture fault.
    """
    try:
        lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return False, f"could not read the app log: {exc}"
    for index in range(len(lines) - 1, -1, -1):
        if "Automatic Writing HUD selection monitor started" in lines[index]:
            lines = lines[index:]
            break
    for line in reversed(lines[-4000:]):
        if "Adaptive Polish came up rules-only" in line:
            return False, line.strip()[-110:]
        if "Adaptive Polish model prewarmed" in line:
            if "loaded=True" in line:
                return True, line.strip()[-110:]
            return False, line.strip()[-110:]
        if "Adaptive Polish setup fell back to rules" in line:
            return False, line.strip()[-110:]
        if "verification failed" in line:
            return False, line.strip()[-110:]
    return False, "the app has not reported loading a model yet"


def log_line_count() -> int:
    """How long the app log is now, so a run can read only its own lines."""
    try:
        return len(
            LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
        )
    except OSError:
        return 0


def rules_fallbacks_since(start_line: int) -> list[str]:
    """Every section the app corrected with rules alone during this run.

    The app logs one WARNING per refused section, naming the guard. Read from
    `start_line` so an earlier run's refusals are not attributed to this one.
    """
    try:
        lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [
        line.split("reason=", 1)[-1].strip()[:160]
        for line in lines[start_line:]
        if "Writing HUD section fell back to rules" in line
    ]


# Enough accepts to be sure a pass is doing nothing, rather than hitting one
# section the model declined to change. A ten-section essay being re-reviewed
# from the start trips this well before it completes another full pass.
_NO_PROGRESS_ACCEPTS = 6


def whole_document_button(hud):
    """The HUD's own "Check entire document" control.

    Asking the app to fetch the document is far steadier than selecting it
    and pressing a global hotkey: no selection has to survive a foreground
    change, which is the step that failed run after run.
    """
    for control in _descendants(hud):
        try:
            if control.ControlTypeName != "ButtonControl" or not control.IsEnabled:
                continue
            name = str(control.Name).strip().casefold()
        except Exception:  # noqa: BLE001 - UI Automation is best effort
            continue
        if "entire document" in name or name == "document":
            return control
    return None


# Word answers "Call was rejected by callee" whenever it is busy: opening a
# document, repaginating, saving. It means try again, not failure, and a writer
# never meets it because their clicks arrive seconds apart. A script is faster
# than that, so every call into Word has to be willing to wait and ask again.
#
# Without this the batch had to kill Word between essays, and killing Word
# between essays is not how anybody writes. It also hid what the app restart is
# there for - the HUD carrying one document's review into the next - so the
# realistic run was never actually being measured. On 2026-08-15 a whole
# 20-essay run died on essay 1 because a Word left open from the previous run
# was still busy.
_COM_BUSY = (
    -2147418111,  # RPC_E_CALL_REJECTED
    -2147417846,  # RPC_E_SERVERCALL_RETRYLATER
)


def com_retry(call, *, attempts: int = 8, pause: float = 1.5):
    """Make a COM call, waiting out Word's "busy" answer rather than dying."""
    from comtypes import COMError

    for attempt in range(attempts):
        try:
            return call()
        except COMError as exc:
            if exc.hresult not in _COM_BUSY or attempt == attempts - 1:
                raise
            time.sleep(pause * (attempt + 1))
    raise AssertionError("unreachable")


def word_application():
    """The running Word, or a new one. Word need not already be open.

    ``GetActiveObject`` raises "Operation unavailable" when Word is not
    running, which ended a run before it opened anything.
    """
    try:
        return _active_word_application()
    except OSError:
        import comtypes.client

        word = comtypes.client.CreateObject("Word.Application", dynamic=True)
        word.Visible = True
        time.sleep(2.0)
        return word


def _word_window_handle(stem: str) -> int:
    """The handle of the Word window showing `stem`, by direct Win32 read.

    NOT `automation.GetRootControl().GetChildren()`, which is what this used to
    be and which cost the release gate three essays on 2026-08-30. That call
    enumerates every top-level window on the desktop and reads each one's Name
    through UI Automation. The per-window reads were guarded here; the
    ENUMERATION was not, and it is the enumeration that fails - `GetChildren`
    walks siblings internally, so one slow or dying window raises
    `COMError (-2146233083)` out of `GetNextSiblingControl` and takes the whole
    batch with it. Three essays died that way with presses=0 after 300 seconds
    each, and the same error is recorded against this harness on 2026-08-26 and
    2026-08-28.

    `full_page_user_journey_probe._find_window_handle_by_prefix` already solved
    this, for the speed half of the same problem: EnumWindows plus
    GetWindowTextW is a direct Win32 read costing microseconds, and it cannot
    raise COMError because no UI Automation is involved. This is that approach,
    matching on a suffix and a substring instead of a prefix.

    A window that misbehaves is now simply a window this skips.
    """
    user32 = ctypes.windll.user32
    proto = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)
    )
    found = ctypes.c_int(0)

    def callback(hwnd, result):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value
        if title.endswith("- Word") and stem in title:
            result[0] = hwnd
            return False
        return True

    user32.EnumWindows(proto(callback), ctypes.byref(found))
    return int(found.value or 0)


def focus_named_word_window(stem: str) -> int:
    """Bring the Word window showing `stem` to the front. Returns its handle."""
    handle = _word_window_handle(stem)
    if not handle:
        return 0
    user32 = ctypes.windll.user32
    # Ask, then CHECK, and ask again if it did not take.
    #
    # SetForegroundWindow is advisory. Windows refuses it outright when another
    # process owns the foreground and has not yielded, and it reports that by
    # returning false rather than by raising, so a harness that calls it once
    # and moves on believes it has focus while another window still has it.
    # The AttachThreadInput dance below is the standard way to earn the right,
    # and it still loses races against a window that is actively being typed
    # into.
    #
    # That mattered on 2026-08-27: a twenty-essay batch lost 4 of its first 10
    # essays, every one of them to focus, and the app log named the winner nine
    # times - `focus detected title='Claude'`. The recovery paths that call this
    # were retrying an operation that was quietly doing nothing, so they burned
    # their attempts without ever taking the foreground back.
    for _attempt in range(3):
        user32.ShowWindow(handle, 9)  # SW_RESTORE
        current = user32.GetWindowThreadProcessId(
            user32.GetForegroundWindow(), None
        )
        target = user32.GetWindowThreadProcessId(handle, None)
        user32.AttachThreadInput(current, target, True)
        user32.BringWindowToTop(handle)
        user32.SetForegroundWindow(handle)
        user32.AttachThreadInput(current, target, False)
        time.sleep(1.0)
        if int(user32.GetForegroundWindow() or 0) == handle:
            return handle
    # Returning the handle either way, because the caller's next move is to ask
    # the app which document it captured, and that answer is better evidence
    # than this one. Reporting failure here would trade a check that can be
    # verified for one that cannot.
    return handle


def visible_text(document) -> str:
    content = com_retry(lambda: str(document.Content.Text or ""))
    text, _spans = _word_visible_text_and_spans(content)
    return text


def paragraphs(value: str) -> list[str]:
    return [line.strip() for line in re.split(r"[\r\n]+", value) if line.strip()]


def review_markdown(name: str, before: str, after: str, report: dict) -> str:
    left = paragraphs(before)
    right = paragraphs(after)
    lines = [
        f"# {name}",
        "",
        f"Corrected {report.get('finished_at')}, "
        f"{report.get('accepted_sections')} sections accepted, "
        f"{report.get('skipped_sections')} skipped, "
        f"{report.get('total_seconds')}s.",
        "",
        "Each row is one paragraph. Read the right column and say whether it "
        "is correct English that still means what the left column meant.",
        "",
    ]
    fell_back = int(report.get("sections_that_fell_back_to_rules") or 0)
    if fell_back:
        # Say it at the top. Otherwise a reader judges these paragraphs as the
        # AI's work when the AI's answer was thrown away, and the thin result
        # reads as a bad model rather than a refused correction.
        lines.extend(
            [
                f"**{fell_back} section(s) got the deterministic rules repair "
                "only - the AI answer was refused.** Spelling and agreement are "
                "fixed in those; sentence structure is not. Reasons:",
                "",
                *(
                    f"- {reason}"
                    for reason in report.get("rules_fallback_reasons") or ()
                ),
                "",
            ]
        )
    if report.get("error"):
        lines.extend([f"**Run reported:** {report['error']}", ""])
    for index in range(max(len(left), len(right))):
        was = left[index] if index < len(left) else ""
        now = right[index] if index < len(right) else ""
        lines.append(f"## Paragraph {index + 1}"
                     + ("" if was != now else "  (unchanged)"))
        lines.append("")
        lines.append(f"**Was:** {was}")
        lines.append("")
        lines.append(f"**Now:** {now}")
        lines.append("")
    return "\n".join(lines)


def run_session(
    essay_path: Path,
    *,
    first_wait: float,
    section_wait: float,
    hotkey: str,
    use_open_document: bool = False,
) -> dict:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    working = OUTPUT_ROOT / essay_path.name

    _dismiss_existing_hud()
    word = word_application()
    if use_open_document:
        # Review whatever the writer already has open and in front. Opening
        # and refocusing a document from a background process is the part
        # that keeps failing - Windows refuses the foreground change, or one
        # of several leftover windows answers to the same title - and none
        # of that is the product under test.
        document = word.ActiveDocument
        working = Path(str(document.FullName))
    # Close EVERY document this harness has open, not only one with the same
    # name as the incoming essay.
    #
    # Closing only the matching name was the defect that made every batch run
    # measure the wrong file. --as-a-writer leaves Word running, so essay 1
    # stayed open, its window kept the foreground, and essay 2's hotkey press
    # captured essay 1: "review prepared sections=10 atomic=0 characters=5065"
    # at 19:09:28 on 2026-08-17, followed by ten more corrections applied to an
    # essay that was already finished, while essay 2 came back byte-identical
    # to its source. The whole point of one document at a time is that the
    # front window is not a guess.
    #
    # Only files under this harness's own output directory are closed, so a
    # writer's own open documents are never touched.
    for open_document in [] if use_open_document else list(word.Documents):
        try:
            full = Path(str(open_document.FullName))
        except Exception:  # noqa: BLE001 - Word raises assorted COM errors
            continue
        if full.name != working.name and full.parent != OUTPUT_ROOT:
            continue
        try:
            open_document.Close(SaveChanges=0)
            time.sleep(1.0)
        except Exception:  # noqa: BLE001 - Word raises assorted COM errors
            continue
    try:
        if not use_open_document:
            shutil.copyfile(essay_path, working)
    except PermissionError:
        for suffix in range(2, 40):
            candidate = OUTPUT_ROOT / f"{essay_path.stem}-{suffix}{essay_path.suffix}"
            try:
                shutil.copyfile(essay_path, candidate)
            except PermissionError:
                continue
            working = candidate
            break
        else:
            raise
    # The corpus arrived in a zip, so every copy carries the mark of the web
    # and Word opens it in Protected View - read only, with no selection to
    # read. The app then reports "No selected text was found" on an essay
    # that is plainly selected on screen. A writer clears this by clicking
    # Enable Editing; the file is local and already trusted here.
    try:
        (working.parent / f"{working.name}:Zone.Identifier").unlink()
    except OSError:
        pass
    if not use_open_document:
        document = com_retry(lambda: word.Documents.Open(str(working.resolve())))
        time.sleep(2.5)

    before = visible_text(document)
    # Marked before the review starts so only this run's refusals are counted.
    run_log_length = log_line_count()
    report: dict = {
        "essay": essay_path.name,
        "document": str(working),
        "characters_before": len(before),
        "sections": [],
    }
    started = time.monotonic()

    # Opening the document parks a caret in it, and the badge that appears
    # for that caret covers only the page around it - 3522 characters of a
    # 5203 character essay, so the last two paragraphs were never offered
    # and came back untouched. Clear that stale badge first: dismissing it
    # after selecting took the focus away from Word and dropped the
    # selection, and the app then answered "No selected text was found" on a
    # fully selected essay.
    _dismiss_existing_hud()
    time.sleep(1.0)
    # Focus the window holding this document, by name. Matching any window
    # whose title ends in "- Word" picked whichever one Windows returned
    # first, and after a few runs that was one of ten leftover windows -
    # several of them in Protected View, where nothing can be selected at
    # all. The app then answered "No selected text was found" while the
    # essay under test sat selected in a window that never came forward.
    # Word has to be the foreground window when the hotkey fires, whoever
    # opened the document. Without this the key went to whatever really was
    # in front and the app answered "No selected text was found" about a
    # document that was fully selected.
    focus_named_word_window(working.stem)
    # Put the caret in the document and let the badge appear by itself. The
    # badge covers only the page around the caret, so the whole document is
    # then requested through the HUD's own button rather than by selecting
    # everything and pressing a hotkey - that selection never survived the
    # foreground change and the app kept being asked to review nothing.
    try:
        word.Selection.HomeKey(6)  # wdStory
    except Exception:  # noqa: BLE001 - Word raises assorted COM errors here
        select_whole_story(word)
    time.sleep(1.5)
    hud = _hud_window(20.0)
    if hud is None:
        press_hud_hotkey(hotkey)
        hud = _hud_window(20.0)
    # "Check entire document" only exists once the badge has been opened, so
    # clicking for it first found nothing and the review silently stayed on
    # page one: 3522 characters of a 5203 character essay, with paragraphs 9
    # and 10 never offered. Open the badge, then widen.
    if hud is not None:
        opening_badge = _badge_button(hud)
        if opening_badge is not None:
            report["badge"] = str(opening_badge.Name)
            _click(opening_badge)
            time.sleep(2.0)
            hud = _hud_window(20.0) or hud
        whole = whole_document_button(hud)
        if whole is not None:
            report["used_whole_document_button"] = True
            _click(whole)
            time.sleep(4.0)
            hud = _hud_window(25.0) or hud
        else:
            report["whole_document_button_missing"] = True
    if hud is None:
        report["error"] = "the Writing HUD never appeared for the selection"
    else:
        # A whole essay takes longer to analyse than the single page the
        # badge used to cover, and clicking while that is still running
        # opened a review the AI never started work on: eleven sections
        # offered, no correction requests at all. Wait for the badge text to
        # stop changing before opening it.
        badge = _badge_button(hud)
        settled = ""
        for _ in range(20):
            if badge is None:
                break
            try:
                current = str(badge.Name)
            except Exception:  # noqa: BLE001 - the HUD may be repainting
                break
            if current and current == settled:
                break
            settled = current
            time.sleep(1.0)
            hud = _hud_window(3.0) or hud
            badge = _badge_button(hud) or badge
        if badge is not None:
            report["badge"] = settled or str(badge.Name)
            _click(badge)
        # Before a single button is pressed, confirm the review on screen was
        # built from THIS document.
        #
        # 2026-08-15: it was not, and nothing noticed. The Claude Code window
        # took focus part-way through a run, the HUD did what it is designed
        # to do and captured the focused window instead, and a review of 26
        # characters of chat text appeared. This harness then clicked Replace
        # five times against it, every apply failed with "No selected text was
        # found", and the run was reported as "sections=5 accepted=5". The
        # essay came back byte-identical and the report said success. A false
        # success is worse than a failure, because it becomes a number.
        # Losing focus once is ordinary on a machine in use, so try to take it
        # back rather than abandoning a 45-minute batch over one blip. Each
        # attempt re-focuses Word, re-triggers the capture, and re-reads what
        # the app says it captured.
        captured = captured_document_title()
        for _attempt in range(3):
            # A missing title used to break out of the loop and let the run
            # continue, so a guard built to catch the wrong document passed
            # whenever it could not read one. Only a title that names this
            # essay is a pass now.
            if captured is not None and working.stem.lower() in captured.lower():
                break
            report.setdefault("focus_recoveries", 0)
            report["focus_recoveries"] += 1
            focus_named_word_window(working.stem)
            time.sleep(1.5)
            press_hud_hotkey(hotkey)
            time.sleep(3.0)
            hud = _hud_window(20.0) or hud
            opening = _badge_button(hud)
            if opening is not None:
                _click(opening)
                time.sleep(2.0)
                hud = _hud_window(20.0) or hud
            captured = captured_document_title()
        if captured is not None and working.stem.lower() not in captured.lower():
            report["error"] = (
                "the review on screen was captured from "
                f"{captured!r}, not from {working.name}. Nothing was clicked. "
                "Another window took focus during the run; close it or leave "
                "the machine alone while this runs."
            )
            report["captured_window"] = captured
            # Nothing was clicked, so the document is untouched and the
            # normal tail has nothing to measure. These mirror what it would
            # have computed, because the caller prints them and a KeyError
            # here would hide the very message this guard exists to deliver.
            report["characters_after"] = len(before)
            report["changed"] = False
            report["accepted_sections"] = 0
            report["skipped_sections"] = 0
            report["sections_that_fell_back_to_rules"] = 0
            report["total_seconds"] = round(time.monotonic() - started, 3)
            report["finished_at"] = datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            )
            return report
        expected = len(paragraphs(before))
        offered = 0
        badge_name = str(report.get("badge") or "")
        match = re.search(r"(\d+)\s+review", badge_name)
        if match:
            offered = int(match.group(1))
            report["sections_offered"] = offered
            if offered < expected:
                # Say so rather than quietly reviewing part of the essay and
                # reporting a clean run.
                report["short_capture"] = (
                    f"the badge offered {offered} sections for an essay of "
                    f"{expected} paragraphs"
                )
        seen: dict[str, int] = {}
        # The counter guard below cannot catch a re-review, because the HUD
        # numbers each pass from where the last one finished - "1 of 10" then
        # "11 of 20" then "21 of 30" - so no counter string ever repeats. On
        # 2026-08-17 a finished essay was reviewed seventeen times and accepted
        # 163 sections in forty minutes without tripping it, and the batch never
        # reached essay 2.
        #
        # So watch the document instead of the label. Accepting corrections that
        # change nothing, over and over, is the definition of no progress
        # whatever the counter says.
        unchanged_accepts = 0
        last_text = before
        while True:
            limit = first_wait if not report["sections"] else section_wait
            wait_started = time.monotonic()
            deadline = wait_started + limit
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
                    report["error"] = (
                        f"section {counter or '?'} offered neither a "
                        f"correction nor a skip within {limit}s"
                    )
                    break
                report["sections"].append(
                    {"counter": counter, "seconds": waited, "accepted": False}
                )
            else:
                if not _click(accept):
                    report["error"] = f"accept click failed at {counter or '?'}"
                    break
                time.sleep(1.2)
                report["sections"].append(
                    {"counter": counter, "seconds": waited, "accepted": True}
                )
                try:
                    current = visible_text(document)
                except Exception:  # noqa: BLE001 - Word raises assorted COM errors
                    current = last_text
                if current == last_text:
                    unchanged_accepts += 1
                else:
                    unchanged_accepts = 0
                    last_text = current
                if unchanged_accepts >= _NO_PROGRESS_ACCEPTS:
                    report["no_progress"] = (
                        f"{unchanged_accepts} accepted sections in a row left "
                        "the document unchanged"
                    )
                    break
            if counter:
                seen[counter] = seen.get(counter, 0) + 1
                if seen[counter] > 3:
                    report["error"] = f"section counter stuck at {counter}"
                    break
            if len(report["sections"]) > 200:
                report["error"] = "exceeded 200 review decisions"
                break
            time.sleep(0.6)
            if _hud_window(2.5) is None:
                break

    after = visible_text(document)
    report["characters_after"] = len(after)
    report["changed"] = after != before
    report["accepted_sections"] = sum(
        1 for value in report["sections"] if value.get("accepted")
    )
    report["skipped_sections"] = sum(
        1 for value in report["sections"] if not value.get("accepted")
    )
    report["total_seconds"] = round(time.monotonic() - started, 3)
    report["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # A section whose AI answer was refused still shows a review card, still
    # says Accept, and still lands here as an accepted section. It carries the
    # deterministic rules repair, which fixes spelling and agreement and leaves
    # the sentence structure alone - so "8 of 8 accepted" can mean eight
    # paragraphs the AI never corrected. The app names each one in its log now,
    # and a run that hides them is not worth reading.
    fallbacks = rules_fallbacks_since(run_log_length)
    report["sections_that_fell_back_to_rules"] = len(fallbacks)
    report["rules_fallback_reasons"] = fallbacks[:20]
    if fallbacks:
        report.setdefault(
            "error",
            f"{len(fallbacks)} of {len(report['sections'])} sections were shown "
            "as corrected but only got the deterministic rules repair",
        )

    # Judge the essay, not the run. Counting accepted sections said "8 of 8,
    # no error" while paragraphs 9 and 10 were never corrected at all, and
    # the badge-text check meant to catch that never ran because the whole
    # document path does not read the badge. Comparing the finished text
    # with the original cannot be sidestepped by a change of route.
    before_paragraphs = paragraphs(before)
    after_paragraphs = paragraphs(after)
    untouched = [
        index + 1
        for index in range(len(before_paragraphs))
        if index < len(after_paragraphs)
        and before_paragraphs[index] == after_paragraphs[index]
    ]
    missing = max(0, len(before_paragraphs) - len(after_paragraphs))
    report["paragraphs"] = len(before_paragraphs)
    report["paragraphs_unchanged"] = untouched
    if untouched or missing:
        report.setdefault(
            "error",
            f"{len(untouched) + missing} of {len(before_paragraphs)} paragraphs "
            f"were never corrected: {untouched or 'missing from the result'}",
        )

    try:
        # Closing the document and leaving Word open is what a writer does, and
        # it is what lets the next essay run in the same session. Both calls
        # wait out a busy Word rather than leaving the file locked for the run
        # after this one.
        com_retry(document.Save)
        com_retry(lambda: document.Close(SaveChanges=0))
    except Exception:  # noqa: BLE001 - Word raises assorted COM errors here
        pass
    _dismiss_existing_hud()

    stem = working.with_suffix("")
    Path(f"{stem}.before.txt").write_text(before, encoding="utf-8")
    Path(f"{stem}.after.txt").write_text(after, encoding="utf-8")
    Path(f"{stem}.review.md").write_text(
        review_markdown(essay_path.stem, before, after, report),
        encoding="utf-8",
    )
    report["review_file"] = f"{stem}.review.md"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Correct one essay in Word and keep the result for review",
    )
    parser.add_argument("--corpus", type=Path, default=CORPUS)
    parser.add_argument("--essay", default="1", help="1-based index or file stem.")
    parser.add_argument("--first-wait", type=float, default=180.0)
    parser.add_argument("--section-wait", type=float, default=150.0)
    parser.add_argument("--hotkey", default="ctrl+shift+space")
    parser.add_argument(
        "--use-open-document",
        action="store_true",
        help="Review the document already open and focused in Word.",
    )
    parser.add_argument(
        "--allow-rules-only",
        action="store_true",
        help="Run even when the local AI is off, to measure the floor.",
    )
    args = parser.parse_args(argv)

    essays = _essay_files(args.corpus)
    if not essays:
        parser.error(f"no essays under {args.corpus}")
    chosen = None
    if args.essay.isdigit():
        index = int(args.essay)
        if 1 <= index <= len(essays):
            chosen = essays[index - 1]
    if chosen is None:
        chosen = next(
            (path for path in essays if args.essay.lower() in path.stem.lower()),
            None,
        )
    if chosen is None:
        parser.error(f"no essay matched {args.essay!r}")

    ready, detail = local_ai_state()
    print(f"local AI loaded: {ready}\n  {detail}", flush=True)
    if not ready and not args.allow_rules_only:
        print(
            "\nRefusing to run. With the AI off the essay comes back barely "
            "changed, which reads as a broken corrector and wastes the run "
            "and the reading after it. Restart the app, wait for "
            "'model prewarmed ... loaded=True', then try again. Use "
            "--allow-rules-only to measure the deterministic floor on purpose.",
            flush=True,
        )
        return 3

    report = run_session(
        chosen,
        first_wait=args.first_wait,
        section_wait=args.section_wait,
        hotkey=args.hotkey,
        use_open_document=args.use_open_document,
    )
    json_path = Path(str(Path(report["document"]).with_suffix("")) + ".json")
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(
        f"\n{report['essay']}: sections={len(report['sections'])} "
        f"accepted={report['accepted_sections']} "
        f"skipped={report['skipped_sections']} "
        f"changed={report['changed']} "
        f"{report['total_seconds']}s "
        f"error={report.get('error', '')}",
        flush=True,
    )
    # A run that stopped before writing a review has no file to point at, and
    # a KeyError here would bury the error message that says why.
    review_file = report.get("review_file")
    if review_file:
        print(f"review this: {review_file}", flush=True)
    return 0 if report.get("changed") and not report.get("error") else 5


if __name__ == "__main__":
    raise SystemExit(main())
