"""Report how much of a Word document the reader captures from a bare caret.

This is the one measurement behind the page-cap defect: opening an essay parks
the caret at the start, the reader expanded that caret to its enclosing page,
and UI Automation cannot see Word's unrendered pages - so a 5203 character
essay captured as 3522 and its last two paragraphs were never offered, while
the run reported every section it did offer as accepted.

It does not use the hotkey. Driving `Ctrl+Shift+Space` from a background
process does not reliably present Word's selection to the app, which cost a
whole session to establish; the reader is called in-process instead, which is
what the app's own capture path calls.

    .venv/Scripts/python.exe tools/word_capture_scope_probe.py --essay 1
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT / "src", ROOT / "tools"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from animal_essay_corpus_probe import CORPUS, _essay_files  # noqa: E402
from echo_link_core.windows_selection import WindowsSelectionReader  # noqa: E402
from word_essay_session import (  # noqa: E402
    focus_named_word_window,
    visible_text,
    word_application,
)

OUTPUT_ROOT = ROOT / "build" / "corrected"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--essay", default="1")
    arguments = parser.parse_args()

    essays = _essay_files(CORPUS)
    index = int(arguments.essay) if arguments.essay.isdigit() else 1
    essay = essays[index - 1]
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    working = OUTPUT_ROOT / f"SCOPE_{essay.name}"
    shutil.copyfile(essay, working)
    try:
        (working.parent / f"{working.name}:Zone.Identifier").unlink()
    except OSError:
        pass

    word = word_application()
    document = word.Documents.Open(str(working.resolve()))
    time.sleep(2.5)
    whole = visible_text(document)
    print(f"document characters (over COM): {len(whole)}")
    print(f"document paragraphs:            {len([p for p in whole.splitlines() if p.strip()])}")

    focus_named_word_window(working.stem)
    # A bare caret at the very start, which is what opening a document leaves
    # and what a writer clicking once in their text produces.
    word.Selection.HomeKey(6)  # wdStory
    time.sleep(1.0)

    reader = WindowsSelectionReader(
        max_characters=500_000,
        include_caret_sentence=True,
        include_word_page=True,
    )
    detected = reader.read()
    captured = 0 if detected is None else len(detected.record.text)
    print(f"captured from a bare caret:     {captured}")
    if detected is not None:
        print(f"scope reported:                 {detected.record.scope}")
    ratio = captured / max(1, len(whole))
    print(f"share of the document:          {ratio:.1%}")

    try:
        document.Close(SaveChanges=0)
    except Exception:  # noqa: BLE001 - Word raises assorted COM errors here
        pass

    if detected is None:
        print("FAIL: nothing was captured")
        return 3
    if ratio < 0.98:
        print("FAIL: the capture is short, so later paragraphs cannot be offered")
        return 4
    print("PASS: the whole document is captured from a bare caret")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
