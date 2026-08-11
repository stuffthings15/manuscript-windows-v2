"""Drive the real Writing HUD through the 20 animal-pet error essays.

The probe reads each .docx essay from the locked local V2 corpus, opens it in real
Windows Notepad, clicks the Manuscript badge, and accepts every review
section in order exactly as a user would. It records the corrected text and
per-section timings so the result can be checked for remaining errors.

Run from ``Manuscript/Windows Version 2`` with the app already running::

    .venv/Scripts/python.exe tools/animal_essay_corpus_probe.py
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
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
    _reports_ai_ready,
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

CORPUS = ROOT / "test-corpus" / "animal-pet-20"
EXPECTED_ESSAY_COUNT = 20
EXPECTED_PARAGRAPH_COUNT = 10
CORPUS_ARCHIVE_SHA256 = (
    "C80A6FBA0978596A464E1E9FF3C17D17D6A928FA56445A97C8E045A0EB9FF771"
)

_WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _essay_files(corpus: Path) -> list[Path]:
    """Every essay in the folder, ignoring Word's own scratch files.

    Word writes a small "~$name.docx" owner file beside any document it has
    open. It is not a document, so reading it raised BadZipFile and the run
    died before reviewing a single essay - which is what happened simply
    because one of the essays was open in Word at the time.
    """
    return sorted(
        path
        for path in corpus.glob("*.docx")
        if not path.name.startswith("~$")
    )


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
    for path in _essay_files(corpus):
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


def validate_corpus(corpus: Path) -> list[str]:
    """Return release-blocking problems with the locked local corpus."""
    failures: list[str] = []
    archive = corpus / "source.zip"
    if not archive.is_file():
        failures.append("locked source.zip is missing")
    else:
        digest = hashlib.sha256(archive.read_bytes()).hexdigest().upper()
        if digest != CORPUS_ARCHIVE_SHA256:
            failures.append("locked source.zip hash changed")

    paths = _essay_files(corpus)
    if len(paths) != EXPECTED_ESSAY_COUNT:
        failures.append(
            f"expected {EXPECTED_ESSAY_COUNT} essays, found {len(paths)}"
        )
    for path in paths:
        try:
            count = len(docx_paragraphs(path))
        except (KeyError, ET.ParseError, zipfile.BadZipFile):
            failures.append(f"{path.name} is not a readable Word document")
            continue
        if count != EXPECTED_PARAGRAPH_COUNT:
            failures.append(
                f"{path.name} has {count} paragraphs; "
                f"expected {EXPECTED_PARAGRAPH_COUNT}"
            )
    return failures


def _paragraphs(text: object) -> list[str]:
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def validate_report(report: dict[str, object]) -> list[str]:
    """Return proof failures that make an essay result unsafe to count."""
    failures: list[str] = []
    paragraph_count = int(report.get("paragraph_count") or 0)
    sections = list(report.get("sections") or [])
    expected_counters = [
        f"{index} of {paragraph_count}"
        for index in range(1, paragraph_count + 1)
    ]
    actual_counters = [str(section.get("counter") or "") for section in sections]
    if actual_counters != expected_counters:
        failures.append(
            f"section counters were not exactly 1 of {paragraph_count} "
            f"through {paragraph_count} of {paragraph_count}"
        )
    if any(
        not section.get("accepted")
        or section.get("skipped")
        or not section.get("ai_review_ready")
        for section in sections
    ):
        failures.append(
            "every section must be AI-reviewed and accepted; "
            "skip or rules-only results are forbidden"
        )

    original_paragraphs = _paragraphs(report.get("original_text"))
    final_paragraphs = _paragraphs(report.get("final_text"))
    text_is_complete = (
        paragraph_count > 0
        and len(original_paragraphs) == paragraph_count
        and len(final_paragraphs) == paragraph_count
        and len(str(report.get("final_text") or ""))
        >= 0.65 * len(str(report.get("original_text") or ""))
        and all(
            len(after) >= 0.45 * len(before)
            for before, after in zip(original_paragraphs, final_paragraphs)
        )
    )
    if not text_is_complete:
        failures.append("final text was truncated or lost paragraph boundaries")
    if report.get("error"):
        failures.append(f"probe error: {report['error']}")
    if not report.get("completed"):
        failures.append("essay review did not complete")
    if not report.get("changed"):
        failures.append("essay text was not corrected")
    return failures


def build_summary(
    reports: list[dict[str, object]],
    *,
    generated_at: str | None = None,
) -> dict[str, object]:
    """Build a release summary that can pass only with all 20 essays."""
    for report in reports:
        report["validation_failures"] = validate_report(report)
    unique_essays = len({str(report.get("page_id") or "") for report in reports})
    return {
        "schema_version": 2,
        "probe": "animal_essay_corpus",
        "generated_at": generated_at
        or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "required_essays": EXPECTED_ESSAY_COUNT,
        "reported_essays": len(reports),
        "unique_essays": unique_essays,
        "completed_essays": sum(
            1 for report in reports if not report["validation_failures"]
        ),
        "passed": len(reports) == EXPECTED_ESSAY_COUNT
        and unique_essays == EXPECTED_ESSAY_COUNT
        and all(not report["validation_failures"] for report in reports),
        "essays": reports,
    }


def run_essay(essay, *, first_wait: float, section_wait: float) -> dict[str, object]:
    """Work through one essay exactly as a person would.

    Accept each section only after the HUD proves its AI correction is ready.
    A skipped, rules-only, missing, or out-of-order section fails the probe.
    """
    _dismiss_existing_hud()
    result: dict[str, object] = {
        "page_id": essay["page_id"],
        "characters": len(str(essay["text"])),
        "original_text": str(essay["text"]),
        "paragraph_count": int(essay["paragraph_count"]),
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
            ai_ready = False
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
                except Exception:  # noqa: BLE001 - the HUD may be repainting
                    hud = None
                    continue
                ai_ready = bool(
                    accept is not None
                    and accept.IsEnabled
                    and _reports_ai_ready(hud, accept)
                )
                if ai_ready and counter:
                    break
                time.sleep(0.25)
            waited = round(time.monotonic() - wait_started, 3)
            if hud is None:
                break
            if accept is None or not ai_ready:
                result["error"] = (
                    f"section {counter or '?'} did not produce an AI-reviewed "
                    f"correction within {wait_limit}s"
                )
                return result
            expected_counter = (
                f"{len(result['sections']) + 1} of {essay['paragraph_count']}"
            )
            if counter != expected_counter:
                result["error"] = (
                    f"out-of-order section: expected {expected_counter}, got "
                    f"{counter or '?'}"
                )
                return result
            if not _click(accept):
                result["error"] = f"accept click failed at {counter or '?'}"
                return result
            time.sleep(0.9)
            result["sections"].append(
                {
                    "counter": counter,
                    "wait_seconds": waited,
                    "accepted": True,
                    "skipped": False,
                    "ai_review_ready": True,
                    "doc_chars": len(_document_text(handle)),
                }
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
        result["completed"] = (
            len(result["sections"]) == int(essay["paragraph_count"])
        )
        if not result["completed"]:
            result["error"] = (
                f"review ended after {len(result['sections'])} of "
                f"{essay['paragraph_count']} sections"
            )
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
    corpus_failures = validate_corpus(args.corpus)
    if corpus_failures:
        parser.error("; ".join(corpus_failures))
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
        final_text = str(report.get("final_text") or "")
        report["changed"] = final_text.strip() != str(essay["text"]).strip()
        report["validation_failures"] = validate_report(report)
        reports.append(report)
        status = "PASS" if not report["validation_failures"] else "FAIL"
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
            json.dumps(build_summary(reports), indent=2, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )

    print(args.output)
    return 0 if build_summary(reports)["passed"] else 5


if __name__ == "__main__":
    raise SystemExit(main())
