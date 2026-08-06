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
    focus_word,
    press_hud_hotkey,
    select_whole_story,
    skip_button,
)
from writing_hud_apply_probe import (  # noqa: E402
    _accept_button,
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
    """
    try:
        lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return False, f"could not read the app log: {exc}"
    for line in reversed(lines[-4000:]):
        if "Adaptive Polish model prewarmed" in line:
            if "loaded=True" in line:
                return True, line.strip()[-110:]
            return False, line.strip()[-110:]
        if "Adaptive Polish setup fell back to rules" in line:
            return False, line.strip()[-110:]
        if "verification failed" in line:
            return False, line.strip()[-110:]
    return False, "the app has not reported loading a model yet"


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
) -> dict:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    working = OUTPUT_ROOT / essay_path.name

    _dismiss_existing_hud()
    word = word_application()
    # The previous run's copy is usually still open, and Word holds it, so
    # replacing it fails outright. Close that document first, and if it is
    # locked for any other reason use the next free name rather than losing
    # the run at its first step.
    for open_document in list(word.Documents):
        try:
            if Path(str(open_document.FullName)).name == working.name:
                open_document.Close(SaveChanges=0)
                time.sleep(1.0)
        except Exception:  # noqa: BLE001 - Word raises assorted COM errors
            continue
    try:
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
    document = word.Documents.Open(str(working.resolve()))
    time.sleep(2.5)

    before = visible_text(document)
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
    focus_named_word_window(working.stem)
    select_whole_story(word)
    press_hud_hotkey(hotkey)
    hud = _hud_window(25.0)
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
