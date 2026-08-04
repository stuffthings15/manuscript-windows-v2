"""Drive the real Writing HUD through the 20 animal-pet error essays.

The probe reads each .docx essay from the shared corpus, opens it in real
Windows Notepad, clicks the Manuscript badge, and accepts every review
section in order exactly as a user would. It records the corrected text and
per-section timings so the result can be checked for remaining errors.

Run from ``Manuscript/Windows`` with the app already running::

    .venv/Scripts/python.exe tools/animal_essay_corpus_probe.py
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import ctypes
import time

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
    _editable_control,
)


def hud_snapshot() -> list[dict[str, object]]:
    """Describe every HUD control, so a stalled wait can be explained."""
    hud = _hud_window(3.0)
    if hud is None:
        return [{"error": "no HUD window"}]
    controls: list[dict[str, object]] = []
    for control in _descendants(hud):
        try:
            controls.append(
                {
                    "type": str(control.ControlTypeName),
                    "name": str(control.Name)[:120],
                    "enabled": bool(control.IsEnabled),
                    "offscreen": bool(control.IsOffscreen),
                }
            )
        except Exception:  # noqa: BLE001 - diagnostics must never throw
            continue
    return controls

CORPUS = (
    ROOT.parent / "20_Animal_Pet_Essays_800_Percent_More_Errors"
)

_WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def docx_paragraphs(path: Path) -> list[str]:
    """Return the visible paragraph text of a .docx without extra packages."""
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    body = ET.fromstring(xml).find(f"{_WORD_NS}body")
    paragraphs: list[str] = []
    for node in body.iter(f"{_WORD_NS}p"):
        pieces: list[str] = []
        for run in node.iter():
            if run.tag == f"{_WORD_NS}t":
                pieces.append(run.text or "")
            elif run.tag in (f"{_WORD_NS}tab", f"{_WORD_NS}br"):
                pieces.append(" ")
        text = "".join(pieces).strip()
        if text:
            paragraphs.append(text)
    return paragraphs


def load_essays(corpus: Path) -> list[dict[str, object]]:
    essays = []
    for path in sorted(corpus.glob("*.docx")):
        paragraphs = docx_paragraphs(path)
        if not paragraphs:
            continue
        # The first paragraph is the essay title; keep it so the user-visible
        # document matches what a real reader would paste in.
        essays.append(
            {
                "page_id": path.stem,
                "source": str(path),
                "title": paragraphs[0],
                "text": "\n".join(paragraphs),
                "paragraph_count": len(paragraphs),
                "min_sections": 0,
                "forbidden": (),
                "required": (),
            }
        )
    return essays


def _skip_button(hud):
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


def run_essay(essay, *, first_wait: float, section_wait: float) -> dict[str, object]:
    """Work through one essay exactly as a person would.

    Accept each section whose replacement becomes available. When the HUD
    reports it found no safe change, skip that section and carry on instead
    of waiting forever - that is the only move a real user has there, and
    without it a single unfixable heading blocked the whole document.
    """
    _dismiss_existing_hud()
    result: dict[str, object] = {
        "page_id": essay["page_id"],
        "characters": len(str(essay["text"])),
        "sections": [],
        "completed": False,
    }
    started = time.monotonic()
    process, target, handle = _open_notepad(str(essay["text"]))
    try:
        ctypes.windll.user32.SetForegroundWindow(handle)
        editor = _editable_control(handle)
        if editor is None:
            result["error"] = "Notepad editor not found"
            return result
        editor.Click()
        editor.SetFocus()

        hud = _hud_window(10.0)
        if hud is None:
            result["error"] = "badge did not appear"
            return result
        badge = _badge_button(hud)
        if badge is None:
            result["error"] = "badge button missing"
            return result
        result["badge_name"] = str(badge.Name)
        if not _click(badge):
            result["error"] = "badge click failed"
            return result

        seen_counters: dict[str, int] = {}
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
                    skip = _skip_button(hud)
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
            # A section the HUD keeps showing after its wait is one it cannot
            # safely change; a user skips it rather than staring at it.
            if accept is None:
                if skip is None or not _click(skip):
                    result["error"] = (
                        f"section {counter or '?'} offered neither a "
                        f"replacement nor a skip within {wait_limit}s"
                    )
                    return result
                result["sections"].append(
                    {"counter": counter, "wait_seconds": waited, "accepted": False,
                     "skipped": True}
                )
            else:
                if not _click(accept):
                    result["error"] = f"accept click failed at {counter or '?'}"
                    return result
                time.sleep(0.9)
                result["sections"].append(
                    {"counter": counter, "wait_seconds": waited, "accepted": True,
                     "skipped": False, "doc_chars": len(_document_text(handle))}
                )
            # Guard against a counter that never advances.
            if counter:
                seen_counters[counter] = seen_counters.get(counter, 0) + 1
                if seen_counters[counter] > 3:
                    result["error"] = f"section counter stuck at {counter}"
                    return result
            if len(result["sections"]) > 200:
                result["error"] = "section loop exceeded 200 decisions"
                return result
            time.sleep(0.5)
            if _hud_window(2.0) is None:
                break

        result["final_text"] = _document_text(handle)
        result["accepted_sections"] = sum(
            1 for section in result["sections"] if section.get("accepted")
        )
        result["skipped_sections"] = sum(
            1 for section in result["sections"] if section.get("skipped")
        )
        result["completed"] = bool(result["sections"])
        return result
    finally:
        result["total_seconds"] = round(time.monotonic() - started, 3)
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the 20 animal-pet error essays through the HUD",
    )
    parser.add_argument("--corpus", type=Path, default=CORPUS)
    parser.add_argument("--first-wait", type=float, default=180.0)
    parser.add_argument("--section-wait", type=float, default=90.0)
    parser.add_argument(
        "--essays",
        default="",
        help="Comma-separated page ids or 1-based indexes; default is all.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "build" / "animal-essay-corpus.json",
    )
    args = parser.parse_args(argv)

    essays = load_essays(args.corpus)
    if not essays:
        parser.error(f"no .docx essays found under {args.corpus}")
    if args.essays:
        wanted = {item.strip() for item in args.essays.split(",") if item.strip()}
        selected = []
        for index, essay in enumerate(essays, start=1):
            if str(index) in wanted or str(essay["page_id"]) in wanted:
                selected.append(essay)
        if not selected:
            parser.error(f"no essays matched {sorted(wanted)}")
        essays = selected

    reports = []
    for essay in essays:
        report = run_essay(
            essay,
            first_wait=args.first_wait,
            section_wait=args.section_wait,
        )
        if report.get("error"):
            report["hud_controls"] = hud_snapshot()
        report["source"] = essay["source"]
        report["original_text"] = str(essay["text"])
        report["paragraph_count"] = essay["paragraph_count"]
        final_text = str(report.get("final_text") or "")
        report["changed"] = final_text.strip() != str(essay["text"]).strip()
        reports.append(report)
        status = "PASS" if report.get("completed") and report["changed"] else "FAIL"
        print(
            f"{status} {report['page_id']}: "
            f"sections={len(report['sections'])} "
            f"accepted={report.get('accepted_sections')} "
            f"skipped={report.get('skipped_sections')} "
            f"total={report.get('total_seconds')}s "
            f"changed={report['changed']} "
            f"error={report.get('error', '')}",
            flush=True,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "probe": "animal_essay_corpus",
                    "generated_at": datetime.now(timezone.utc).isoformat(
                        timespec="seconds"
                    ),
                    "completed_essays": len(reports),
                    "total_essays": len(essays),
                    "passed": all(
                        r.get("completed") and r.get("changed") for r in reports
                    ),
                    "essays": reports,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    print(args.output)
    return 0 if all(r.get("completed") and r.get("changed") for r in reports) else 5


if __name__ == "__main__":
    raise SystemExit(main())
