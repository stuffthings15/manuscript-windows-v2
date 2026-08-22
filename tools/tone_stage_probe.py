"""Show what each stage of the pipeline does to one paragraph's tone.

The product's own pack scores style 4 and PASSES Friendly when
`full_page_tone_quality_probe` sends it a page directly. Through the product,
the same pack scores 3.14 and fails, with the judge saying "readable but lacks
warmth" over and over. The warmth exists in the model and is lost on the way to
the writer, and this prints every step so the losing step can be named rather
than guessed at.

The difference the probe does NOT have is the staging: the product repairs the
text deterministically first and then asks for the tone ON THE REPAIRED TEXT,
guards the answer, cleans it, and can fall back.

    $env:PYTHONPATH = "src"
    .venv/Scripts/python.exe tools/tone_stage_probe.py --tone Friendly --paragraph 2

A DEBUGGING AID, never a quality number. Correction quality is measured through
Word and the HUD, as a writer - see docs/TESTING.md. This exists to locate a
defect, not to score one.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT / "src", ROOT / "tools"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from echo_link_core.polish import PolishEngine, PolishValidator  # noqa: E402
from echo_link_core.polish_types import (  # noqa: E402
    CorrectionRequest,
    PolishMode,
    PolishTask,
)
from grade_corpus import paragraphs  # noqa: E402

ESSAY = (
    ROOT
    / "test-corpus"
    / "error-riddled-20-docx"
    / "01_urban_beekeeping_and_the_ecology_of_cities.docx"
)


def show(label: str, text: str) -> None:
    print(f"\n--- {label} ({len(text)} chars)")  # noqa: T201
    print(" ".join(str(text or "").split())[:600])  # noqa: T201


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tone", default="Friendly")
    parser.add_argument("--paragraph", type=int, default=2)
    parser.add_argument("--essay", type=Path, default=ESSAY)
    arguments = parser.parse_args(argv)

    blocks = [block for block in paragraphs(arguments.essay) if len(block.split()) > 20]
    if arguments.paragraph > len(blocks):
        print(f"only {len(blocks)} prose paragraphs in this essay")  # noqa: T201
        return 1
    source = blocks[arguments.paragraph - 1]

    seen: list[dict[str, object]] = []
    original_validate = PolishValidator.validate

    def recording_validate(self, source_text, candidate, **kwargs):
        result = original_validate(self, source_text, candidate, **kwargs)
        seen.append(
            {
                "candidate": candidate,
                "accepted": bool(result.accepted),
                "failures": tuple(result.failures),
                "prepared": kwargs.get("prepared"),
            }
        )
        return result

    PolishValidator.validate = recording_validate  # type: ignore[method-assign]
    try:
        engine = PolishEngine()
        result = engine.analyze(
            CorrectionRequest(
                text=source,
                task=PolishTask.TONE,
                mode=PolishMode("balanced"),
                command=arguments.tone,
                download_consent=True,
                manual=True,
            )
        )
    finally:
        PolishValidator.validate = original_validate  # type: ignore[method-assign]

    show("1. WHAT THE WRITER WROTE", source)
    prepared = next(
        (str(entry["prepared"]) for entry in seen if entry.get("prepared")), ""
    )
    if prepared:
        show("2. AFTER THE DETERMINISTIC REPAIR - what the model is actually given", prepared)
    for index, entry in enumerate(seen, start=1):
        verdict = "ACCEPTED" if entry["accepted"] else "REFUSED"
        show(
            f"3.{index} MODEL ANSWER [{verdict}] {'; '.join(entry['failures']) or ''}",
            str(entry["candidate"]),
        )
    show("4. WHAT THE WRITER GETS", str(result.text))

    print("\n--- verdict")  # noqa: T201
    print(f"used_llm         {getattr(result, 'used_llm', None)}")  # noqa: T201
    print(f"rejected_llm     {getattr(result, 'rejected_llm', None)}")  # noqa: T201
    print(f"quality_complete {getattr(result, 'quality_complete', None)}")  # noqa: T201
    print(f"review_ready     {getattr(result, 'review_ready', None)}")  # noqa: T201
    print(f"residual_errors  {getattr(result, 'residual_errors', None)}")  # noqa: T201
    print(f"tone_verified    {getattr(result, 'tone_verified', None)}")  # noqa: T201
    same_as_prepared = prepared and " ".join(str(result.text).split()) == " ".join(
        prepared.split()
    )
    print(  # noqa: T201
        "\nThe writer got the DETERMINISTIC REPAIR, not a tone."
        if same_as_prepared
        else "\nThe writer got something the model produced."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
