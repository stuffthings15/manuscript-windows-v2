"""Save the essay Word is holding and write a before/after file to read.

The point of the exercise is to judge the corrected text, not the run. This
saves whatever is open in Word, pulls the source essay out of the locked
corpus for comparison, and writes one paragraph per row.

    .venv/Scripts/python.exe tools/dump_corrected_essay.py
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT / "src", ROOT / "tools"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from animal_essay_corpus_probe import CORPUS, load_essays  # noqa: E402


def paragraphs(value: str) -> list[str]:
    return [line.strip() for line in value.replace("\r", "\n").split("\n") if line.strip()]


def saved_document_text() -> str:
    import comtypes.client

    word = comtypes.client.GetActiveObject("Word.Application", dynamic=True)
    document = word.ActiveDocument
    document.Save()
    return str(document.Content.Text or "")


def main() -> int:
    after = paragraphs(saved_document_text())
    before = paragraphs(str(load_essays(CORPUS)[0]["text"]))

    out = ROOT / "build" / "corrected" / "essay1-review.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Essay 1 after the Word review",
        "",
        f"Paragraphs before: {len(before)}  after: {len(after)}",
        "",
    ]
    for index in range(max(len(before), len(after))):
        was = before[index] if index < len(before) else "<missing>"
        now = after[index] if index < len(after) else "<missing>"
        lines.append(f"## Paragraph {index + 1}")
        lines.append("")
        lines.append(f"**WAS:** {was}")
        lines.append("")
        lines.append(f"**NOW:** {now}")
        lines.append("")
        if was == now:
            lines.append("**UNCHANGED**")
            lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(out)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
