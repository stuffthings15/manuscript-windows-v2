"""Reproduce the section-3 stall in essays 13 and 18 without Word.

Both essays stop at section 3 in 34.5 seconds in every batch run, which is the
consistency that separates a real defect from this corpus's large run-to-run
variance. A section whose correction equals its original leaves Replace
disabled, and the review cannot advance - that is the shape to look for.

    .venv/Scripts/python.exe tools/stalled_section_probe.py
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT / "src", ROOT / "tools"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from animal_essay_corpus_probe import CORPUS, load_essays  # noqa: E402
from echo_link_core.polish import PolishEngine  # noqa: E402
from echo_link_core.polish_types import (  # noqa: E402
    CorrectionRequest,
    PolishMode,
    PolishTask,
)


def main() -> int:
    essays = load_essays(CORPUS)
    engine = PolishEngine()
    for number in (13, 18):
        document = str(essays[number - 1]["text"])
        paragraphs = [line for line in document.splitlines() if line.strip()]
        print(f"=== essay {number}: {len(paragraphs)} paragraphs ===")
        for position in (3, 4):
            if position > len(paragraphs):
                continue
            source = paragraphs[position - 1]
            result = engine.analyze(
                CorrectionRequest(
                    text=source,
                    task=PolishTask.PROOFREAD,
                    mode=PolishMode("balanced"),
                    command="Proofread",
                    instruction="",
                    document=document,
                    download_consent=True,
                    manual=True,
                )
            )
            same = str(result.text).strip() == source.strip()
            print(f"  paragraph {position}: used_llm={result.used_llm} "
                  f"complete={result.quality_complete} "
                  f"review_ready={result.review_ready} "
                  f"UNCHANGED={same}")
            print(f"    validation: {result.validation}")
            print(f"    message   : {result.message}")
            if same:
                print(f"    SOURCE: {source[:200]}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
