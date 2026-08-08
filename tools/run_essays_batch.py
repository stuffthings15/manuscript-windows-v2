"""Drive essays through the Writing HUD in Word, then judge the text.

One essay per Word session, accepting every section the way a writer does,
then saving the document and scoring what came back. The score is deliberately
objective, because reading twenty essays by eye is how defects get missed:

  * paragraphs the review never changed
  * words the bundled dictionary does not know, which are residual misspellings
  * punctuation the corrector introduced (".," ",," " ,")

Writes build/corrected/<name>.review.md per essay and a summary table.

    .venv/Scripts/python.exe tools/run_essays_batch.py --from 2 --to 20
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT / "src", ROOT / "tools"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from animal_essay_corpus_probe import CORPUS, _essay_files, load_essays  # noqa: E402
from repro_accept_advance import (  # noqa: E402
    card_text,
    counter_text,
    hud_window,
    replace_button,
)
from repro_zero_of_zero import (  # noqa: E402
    click_in_document,
    foreground_word,
    press_hotkey,
)

WORD = re.compile(r"[A-Za-z][A-Za-z']*")
ARTEFACT = re.compile(r"[.!?]\s*,|,\s*,|\s+,|\(\s|\s\)")


def dismiss_button(hud):
    """The control a reader uses when a section has no finished correction.

    The HUD disables Replace for such a section and enables Dismiss, telling
    the reader that later sections keep processing. This harness only knew
    about Replace, so it sat on that one section until it timed out and
    reported the essay as stalled - essays 13 and 18 were called a
    reproducible app defect on that evidence, and they are not one.
    """
    from repro_accept_advance import _descend

    for control in _descend(hud):
        try:
            if control.ControlTypeName != "ButtonControl":
                continue
            if str(control.Name or "").startswith("Dismiss"):
                return control
        except Exception:  # noqa: BLE001 - the HUD may be repainting
            continue
    return None


def invoke(control) -> bool:
    try:
        control.GetInvokePattern().Invoke()
        return True
    except Exception:  # noqa: BLE001 - fall back to a real click
        try:
            control.Click(simulateMove=False)
            return True
        except Exception:  # noqa: BLE001 - the HUD may have closed
            return False


def close_word() -> None:
    import subprocess

    subprocess.run(
        ["taskkill", "/IM", "WINWORD.EXE", "/F"],
        capture_output=True,
        check=False,
    )
    time.sleep(2.0)


def restart_app() -> bool:
    """Start a fresh app before each essay, and wait for the model to load.

    The Writing HUD carried one document's review into the next document's run
    and reported "4 of 11" for a ten-section essay. Essay 18 was called a
    reproducible defect on that evidence and completes perfectly against a
    freshly started app. Sharing one process across a batch measures the batch,
    not the essay.
    """
    import subprocess

    done = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "restart_app_and_wait.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        print(f"   restart failed: {done.stdout.strip()[-200:]}", flush=True)
        return False
    return True


def outcome(row: dict) -> str:
    """One word for what happened, so repeats can be counted rather than read."""
    if row.get("error"):
        return "error"
    if row["presses"] == 0:
        return "dead"
    if row["unchanged"] or row["stalled_at"]:
        return "partial"
    return "complete"


LOG = Path(os.environ.get("LOCALAPPDATA", "")) / "ManuscriptWindowsV2" / "echo-link.log"


def log_tail(lines: int = 60) -> list[str]:
    """The app's own account of the window that just failed."""
    try:
        text = LOG.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 - the diagnosis says so itself
        return [f"<could not read {LOG}: {type(exc).__name__}: {exc}>"]
    kept = [
        line
        for line in text.splitlines()
        if "faster_whisper" not in line and "VAD filter" not in line
    ]
    return kept[-lines:]


def hud_tree(hud, limit: int = 60) -> list[str]:
    """Every control the HUD is showing, named and enabled-or-not."""
    if hud is None:
        return ["<no Writing HUD window>"]
    from repro_accept_advance import _descend

    rows: list[str] = []
    for control in _descend(hud):
        try:
            rows.append(
                f"{control.ControlTypeName:<22} enabled={str(control.IsEnabled):<5} "
                f"name={str(control.Name or '')[:90]!r}"
            )
        except Exception as exc:  # noqa: BLE001 - a repainting control is data too
            rows.append(f"<unreadable control: {type(exc).__name__}: {exc}>")
        if len(rows) >= limit:
            rows.append(f"<truncated at {limit} controls>")
            break
    return rows


def diagnose(reason: str, hud) -> list[str]:
    """Capture why a run failed, at the moment it failed.

    A bare "dead" in the table is unreproducible by the time anyone reads it.
    Four essays were recorded that way in the full batch and each had to be
    chased by hand afterwards. Every timeout now carries its own evidence.
    """
    block = [f"### {reason}", "", "**HUD controls at the moment of failure**", ""]
    block += [f"    {row}" for row in hud_tree(hud)]
    block += ["", "**Application log**", ""]
    block += [f"    {row}" for row in log_tail()]
    block += [""]
    return block


def open_in_word(path: Path):
    import comtypes.client

    word = comtypes.client.CreateObject("Word.Application", dynamic=True)
    word.Visible = True
    word.Documents.Open(str(path.resolve()))
    time.sleep(3.0)
    return word


def paragraphs(value: str) -> list[str]:
    return [
        line.strip()
        for line in str(value).replace("\r", "\n").split("\n")
        if line.strip()
    ]


def review_one(path: Path, *, max_presses: int = 30) -> dict:
    """Accept every section, then return the saved document text."""
    word = open_in_word(path)
    _handle, bounds = foreground_word()
    click_in_document(bounds)
    press_hotkey()

    report: dict = {"presses": 0, "stalled_at": None, "counters": []}
    hud = hud_window(40.0)
    if hud is None:
        report["error"] = "the Writing HUD never appeared"
        report["after"] = str(word.ActiveDocument.Content.Text or "")
        return report

    report["diagnosis"] = []
    deadline = time.monotonic() + 120.0
    reviewed = False
    while time.monotonic() < deadline:
        if counter_text(hud) not in ("<none>", "0 of 0", ""):
            reviewed = True
            break
        time.sleep(1.0)
    if not reviewed:
        # The failure that killed essays 5, 7, 11 and 20, and essay 18 on a
        # freshly restarted app. Record it here rather than leaving a bare
        # "dead" that has to be reproduced by hand later.
        report["timed_out"] = True
        report["diagnosis"] = diagnose(
            "First review never appeared within 120 seconds",
            hud_window(5.0) or hud,
        )

    for press in range(1, max_presses + 1):
        hud = hud_window(10.0)
        if hud is None:
            break
        before = counter_text(hud)
        button = replace_button(hud)
        if button is None:
            break
        if not button.IsEnabled:
            time.sleep(12.0)
            button = replace_button(hud)
            if button is None or not button.IsEnabled:
                # No correction to apply here. Do what a reader does rather
                # than calling the essay stalled: dismiss and keep going.
                skip = dismiss_button(hud)
                if skip is not None and skip.IsEnabled and invoke(skip):
                    report["dismissed"] = report.get("dismissed", 0) + 1
                    report["counters"].append(f"{before}->dismissed")
                    time.sleep(4.0)
                    continue
                report["stalled_at"] = before
                report["diagnosis"] = diagnose(
                    f"Replace and Dismiss both unavailable at {before}", hud
                )
                break
        if not invoke(button):
            break
        report["presses"] = press
        time.sleep(6.0)
        hud = hud_window(8.0)
        if hud is None:
            break
        after = counter_text(hud)
        report["counters"].append(f"{before}->{after}")
        if after == before:
            time.sleep(10.0)
            again = counter_text(hud_window(8.0) or hud)
            if again == before:
                # Do NOT dismiss here, although the captured evidence shows
                # Dismiss enabled at this moment. Measured over three runs,
                # dismissing on a counter that has merely paused threw away
                # eight sections whose corrections were still arriving and
                # turned complete 2/3 into partial 2/3, dead 1/3. A paused
                # counter means wait; only a disabled Replace means skip.
                report["stalled_at"] = before
                report["diagnosis"] = diagnose(
                    f"Counter stopped advancing at {before}", hud
                )
                break

    document = word.ActiveDocument
    try:
        document.Save()
    except Exception:  # noqa: BLE001 - report the text we can still read
        pass
    report["after"] = str(document.Content.Text or "")
    return report


def score(before_text: str, after_text: str, dictionary) -> dict:
    before = paragraphs(before_text)
    after = paragraphs(after_text)
    unchanged = [
        index + 1
        for index in range(min(len(before), len(after)))
        if before[index] == after[index]
    ]
    unknown: list[str] = []
    for paragraph in after:
        for match in WORD.finditer(paragraph):
            token = match.group(0)
            if token[:1].isupper():
                continue  # names are not the speller's business
            if not dictionary.contains(token.casefold()):
                unknown.append(token)
    artefacts = [match.group(0) for match in ARTEFACT.finditer("\n".join(after))]
    return {
        "paragraphs_before": len(before),
        "paragraphs_after": len(after),
        "unchanged": unchanged,
        "unknown_words": sorted(set(unknown)),
        "artefacts": sorted(set(artefacts)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="start", type=int, default=2)
    parser.add_argument("--to", dest="end", type=int, default=20)
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="runs per essay; one run is a hypothesis, not a result",
    )
    parser.add_argument(
        "--no-restart",
        action="store_true",
        help="do not restart the app between essays (measures the batch, not the essay)",
    )
    args = parser.parse_args()

    from echo_link_core.polish_spelling import EnglishDictionary

    dictionary = EnglishDictionary()
    dictionary.warm()

    essays = _essay_files(CORPUS)
    corpus_text = [str(item.get("text", "")) for item in load_essays(CORPUS)]
    work = ROOT / "build" / "batch"
    work.mkdir(parents=True, exist_ok=True)
    out_dir = ROOT / "build" / "corrected"
    out_dir.mkdir(parents=True, exist_ok=True)

    summary: list[dict] = []
    for number in range(args.start, args.end + 1):
        if number > len(essays):
            break
        source = essays[number - 1]
        target = work / source.name
        before_text = str(corpus_text[number - 1])

        outcomes: list[str] = []
        for attempt in range(1, max(1, args.repeat) + 1):
            close_word()  # release the previous run's lock before copying
            shutil.copy2(source, target)  # every run starts from the original
            if not args.no_restart:
                restart_app()
            started = time.monotonic()
            try:
                result = review_one(target)
            except Exception as exc:  # noqa: BLE001 - keep going through the corpus
                result = {"error": f"{type(exc).__name__}: {exc}", "after": ""}
            elapsed = round(time.monotonic() - started, 1)

            judged = score(before_text, result.get("after", ""), dictionary)
            row = {
                "essay": source.stem,
                "number": number,
                "attempt": attempt,
                "seconds": elapsed,
                "presses": result.get("presses", 0),
                "dismissed": result.get("dismissed", 0),
                "stalled_at": result.get("stalled_at"),
                "error": result.get("error"),
                "timed_out": bool(result.get("timed_out")),
                **judged,
            }
            row["outcome"] = outcome(row)
            outcomes.append(row["outcome"])
            summary.append(row)
            print(
                f"{number:>2} {source.stem[:30]:<30} run{attempt} "
                f"{elapsed:>6}s {row['outcome']:<8} presses={row['presses']:>2} "
                f"dismissed={row['dismissed']:>2} "
                f"unchanged={len(row['unchanged']):>2} "
                f"unknown={len(row['unknown_words']):>3} "
                f"artefacts={len(row['artefacts']):>2} "
                f"stalled={row['stalled_at']} err={row['error']}",
                flush=True,
            )

        if args.repeat > 1:
            tally = {name: outcomes.count(name) for name in sorted(set(outcomes))}
            summary_line = "  ".join(
                f"{name} {count}/{len(outcomes)}" for name, count in tally.items()
            )
            print(f"   -> {source.stem[:30]:<30} {summary_line}", flush=True)

        lines = [f"# {source.stem}", ""]
        diagnosis = result.get("diagnosis") or []
        if diagnosis:
            # Put it first. A failure the reader has to scroll past the whole
            # document to find is a failure nobody reads.
            lines += ["## Why this run failed", ""] + diagnosis
        b = paragraphs(before_text)
        a = paragraphs(result.get("after", ""))
        for index in range(max(len(b), len(a))):
            lines.append(f"## Paragraph {index + 1}")
            lines.append("")
            lines.append(f"**WAS:** {b[index] if index < len(b) else '<missing>'}")
            lines.append("")
            lines.append(f"**NOW:** {a[index] if index < len(a) else '<missing>'}")
            lines.append("")
        (out_dir / f"{source.stem}.review.md").write_text(
            "\n".join(lines), encoding="utf-8"
        )

    (ROOT / "build" / "batch-summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    close_word()
    print(f"\nsummary: {ROOT / 'build' / 'batch-summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
