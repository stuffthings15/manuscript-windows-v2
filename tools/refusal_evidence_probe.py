"""Show the answer each meaning guard refused, next to the writer's text.

The coverage probe says which guard refused a paragraph. It cannot say whether
the guard was right. That needs the model's actual answer, and the engine
discards it. This records every rejected candidate as the validator sees it, so
a guard can be judged on the sentence that tripped it rather than on its name.

    $env:PYTHONPATH = "src"
    .venv/Scripts/python.exe tools/refusal_evidence_probe.py --essays 1,2

Writes build/refusal-evidence[-label].md. Read it and decide, per case, whether
the refusal protected the writer's meaning or threw away a good correction.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT / "src", ROOT / "tools"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from animal_essay_corpus_probe import CORPUS, load_essays  # noqa: E402
from echo_link_core.polish import PolishEngine, PolishValidator  # noqa: E402
from echo_link_core.polish_types import (  # noqa: E402
    CorrectionRequest,
    PolishMode,
    PolishTask,
)


def paragraphs(text: str) -> list[str]:
    return [line.strip() for line in str(text).split("\n") if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--essays", default="1,2")
    parser.add_argument("--label", default="")
    arguments = parser.parse_args()
    wanted = {
        int(piece)
        for piece in arguments.essays.replace(" ", "").split(",")
        if piece.isdigit()
    }

    rejected: list[dict[str, object]] = []
    original_validate = PolishValidator.validate

    def recording_validate(self, source, candidate, **kwargs):
        result = original_validate(self, source, candidate, **kwargs)
        if not result.accepted:
            rejected.append(
                {
                    "source": source,
                    "candidate": candidate,
                    "failures": tuple(result.failures),
                    # The meaning guards compare against `prepared` when it is
                    # given. A refusal for a word the rules layer had already
                    # spelled correctly means this arrived as None.
                    "prepared": kwargs.get("prepared"),
                }
            )
        return result

    PolishValidator.validate = recording_validate  # type: ignore[method-assign]
    try:
        essays = load_essays(CORPUS)
        engine = PolishEngine()
        lines: list[str] = ["# Refused corrections, with the answer refused", ""]
        for number, essay in enumerate(essays, start=1):
            if wanted and number not in wanted:
                continue
            name = str(essay.get("name") or f"essay-{number}")
            for index, piece in enumerate(paragraphs(str(essay["text"])), start=1):
                rejected.clear()
                result = engine.analyze(
                    CorrectionRequest(
                        text=piece,
                        task=PolishTask.PROOFREAD,
                        mode=PolishMode("balanced"),
                        command="Proofread",
                        download_consent=True,
                        manual=True,
                    )
                )
                if result.used_llm or not rejected:
                    continue
                lines.append(f"## {name} paragraph {index}")
                lines.append("")
                for order, entry in enumerate(rejected, start=1):
                    lines.append(f"### refusal {order}: {'; '.join(entry['failures'])}")
                    lines.append("")
                    lines.append(f"**Writer:** {entry['source']}")
                    lines.append("")
                    lines.append(f"**Refused answer:** {entry['candidate']}")
                    lines.append("")
                    prepared = entry["prepared"]
                    lines.append(
                        "**Compared against:** "
                        + ("(the raw source - no prepared text was passed)"
                           if prepared is None else str(prepared))
                    )
                    lines.append("")
                print(f"{name} p{index}: {len(rejected)} refusals", flush=True)
    finally:
        PolishValidator.validate = original_validate  # type: ignore[method-assign]

    suffix = f"-{arguments.label}" if arguments.label else ""
    output = ROOT / "build" / f"refusal-evidence{suffix}.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
