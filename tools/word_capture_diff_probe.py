"""Compare the active Word document as COM sees it and as UI Automation sees it.

Replace Text refuses with "refused changed document text" when the document
COM reads back does not match the text the HUD captured through UI Automation.
When the document has not actually been edited, the disagreement is in how the
two APIs render the same characters, and the refusal message cannot say where.

This reads the active document both ways and prints the first difference.
Read-only: it never writes to the document.

    .venv/Scripts/python.exe tools/word_capture_diff_probe.py
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import uiautomation as automation  # noqa: E402

from echo_link_core.insertion import (  # noqa: E402
    _active_word_application,
    _canonical_word_text,
    _word_visible_text_and_spans,
)


def _com_text() -> tuple[str, str]:
    # Same accessor the app uses, so the probe sees exactly what it sees.
    word = _active_word_application()
    document = word.ActiveDocument
    return str(document.Name or ""), str(document.Content.Text or "")


def _uia_text() -> tuple[str, str]:
    root = automation.GetRootControl()
    window = root.WindowControl(searchDepth=2, RegexName=r".*- Word$")
    if not window.Exists(2.0, 0.2):
        raise SystemExit("No Word window found.")
    title = str(window.Name or "")
    for control in window.GetChildren():
        pattern = None
        try:
            pattern = control.GetTextPattern()
        except Exception:  # noqa: BLE001 - most children have no text pattern
            pattern = None
        if pattern is not None:
            return title, str(pattern.DocumentRange.GetText(-1) or "")
    # Fall back to a depth-first walk for the first control exposing text.
    stack = list(window.GetChildren())
    while stack:
        control = stack.pop()
        try:
            pattern = control.GetTextPattern()
        except Exception:  # noqa: BLE001
            pattern = None
        if pattern is not None:
            return title, str(pattern.DocumentRange.GetText(-1) or "")
        stack.extend(control.GetChildren())
    raise SystemExit("Word window exposed no text pattern.")


def _describe(label: str, value: str) -> None:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    print(f"{label}: {len(value)} chars digest={digest[:16]}")


def main() -> int:
    name, raw_com = _com_text()
    title, raw_uia = _uia_text()
    print(f"document={name!r}")
    print(f"title={title!r}")

    com_visible, _spans = _word_visible_text_and_spans(raw_com)
    uia_canonical = _canonical_word_text(raw_uia).rstrip("\n")

    _describe("COM  visible ", com_visible)
    _describe("UIA  canonical", uia_canonical)

    if com_visible == uia_canonical:
        print("MATCH - the two captures agree.")
        return 0

    limit = min(len(com_visible), len(uia_canonical))
    index = next(
        (i for i in range(limit) if com_visible[i] != uia_canonical[i]),
        limit,
    )
    print(f"FIRST DIFFERENCE at index {index}")
    window = slice(max(0, index - 40), index + 40)
    print(f"  COM: {com_visible[window]!r}")
    print(f"  UIA: {uia_canonical[window]!r}")
    if index < limit:
        print(
            f"  COM char {com_visible[index]!r} (U+{ord(com_visible[index]):04X}) "
            f"vs UIA {uia_canonical[index]!r} (U+{ord(uia_canonical[index]):04X})"
        )
    else:
        print("  One capture is a prefix of the other; they differ in length only.")

    # Which characters appear in one capture and not the other at all.
    only_com = sorted(set(com_visible) - set(uia_canonical))
    only_uia = sorted(set(uia_canonical) - set(com_visible))
    if only_com:
        print(f"  characters only in COM: {[hex(ord(c)) for c in only_com]}")
    if only_uia:
        print(f"  characters only in UIA: {[hex(ord(c)) for c in only_uia]}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
