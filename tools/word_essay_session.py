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


def focus_named_word_window(stem: str) -> int:
    """Bring the Word window showing `stem` to the front. Returns its handle."""
    import uiautomation as automation

    handle = 0
    for control in automation.GetRootControl().GetChildren():
        try:
            if control.ControlTypeName != "WindowControl":
                continue
            name = str(control.Name)
        except Exception:  # noqa: BLE001 - other apps answer slowly
            continue
        if not name.endswith("- Word") or stem not in name:
            continue
        handle = int(control.NativeWindowHandle or 0)
        break
    if not handle:
        return 0
    user32 = ctypes.windll.user32
    user32.ShowWindow(handle, 9)  # SW_RESTORE
    current = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    target = user32.GetWindowThreadProcessId(handle, None)
    user32.AttachThreadInput(current, target, True)
    user32.BringWindowToTop(handle)
    user32.SetForegroundWindow(handle)
    user32.AttachThreadInput(current, target, False)
    time.sleep(1.0)
    return handle


def visible_text(document) -> str:
    text, _spans = _word_visible_text_and_spans(str(document.Content.Text or ""))
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
    # The previous run's copy is usually still open, and Word holds it, so
    # replacing it fails outright. Close that document first, and if it is
    # locked for any other reason use the next free name rather than losing
    # the run at its first step.
    for open_document in [] if use_open_document else list(word.Documents):
        try:
            if Path(str(open_document.FullName)).name == working.name:
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
        document = word.Documents.Open(str(working.resolve()))
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
        document.Save()
        document.Close(SaveChanges=0)
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
    print(f"review this: {report['review_file']}", flush=True)
    return 0 if report.get("changed") and not report.get("error") else 5


if __name__ == "__main__":
    raise SystemExit(main())
