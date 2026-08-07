"""Correct one paragraph with and without the document context hint.

Paragraph 2 of essay 1 is the case: "practice esay 1??" comes back
"practice is easy 1." when the section is corrected alone, and the word meant
is "essay", which the same document spells correctly two paragraphs later.

Also reports whether the hint costs immutable tokens, because an earlier
attempt to use this field made the model drop them on full-page reviews.

    .venv/Scripts/python.exe tools/context_hint_probe.py
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


def context_hint(document: str, limit: int = 240) -> str:
    flat = " ".join(str(document or "").split())
    if len(flat) < 40:
        return ""
    head = flat[:limit].rstrip()
    return (
        "CONTEXT (reference only, never copy or mention it): this text is "
        f'one part of a longer document that begins "{head}". Use it to '
        "choose the word the writer meant."
    )


def main() -> int:
    essay = str(load_essays(CORPUS)[0]["text"])
    paragraphs = [line for line in essay.splitlines() if line.strip()]
    engine = PolishEngine()
    targets = {
        "p2 (esay)": paragraphs[1],
        "p8 (Wether)": paragraphs[7],
        "p9 (Cair)": paragraphs[8],
    }
    variants = [("none", ""), ("240", context_hint(essay, 240)), ("800", context_hint(essay, 800))]
    for label, paragraph in targets.items():
        print(f"=== {label} ===")
        for name, instruction in variants:
            try:
                result = engine.analyze(
                    CorrectionRequest(
                        text=paragraph,
                        task=PolishTask.PROOFREAD,
                        mode=PolishMode("balanced"),
                        command="Proofread",
                        instruction=instruction,
                        download_consent=True,
                        manual=True,
                    )
                )
                print(f"  ctx={name:>4}: llm={result.used_llm} {str(result.text)[:170]!r}")
            except Exception as exc:  # noqa: BLE001 - report, do not hide
                print(f"  ctx={name:>4}: FAILED {exc}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
