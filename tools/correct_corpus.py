"""Correct a directory of essays headlessly, and record what was not corrected.

Two numbers describe this product: the rubric score in ``tools/grade_corpus.py``
and how many sections the model actually corrected. They were being produced by
different means, and one of them by no means at all - ``build/held-out-corrected``
has no tool anywhere in the tree that made it, and ``build/batch`` is output from
2026-08-07 that predates every deterministic rule committed since. A score whose
input cannot be regenerated is not a measurement.

So this reads a source directory, corrects every paragraph through the real
engine, and writes plain-text copies the grader reads directly. Same reader as
the grader, deliberately: ``paragraphs`` is imported from it rather than
reimplemented, so the corrector and the rubric can never disagree about what a
document says.

    $env:PYTHONPATH = "src"
    .venv/Scripts/python.exe tools/correct_corpus.py --source test-corpus/animal-pet-20 --out build/corpus-corrected
    .venv/Scripts/python.exe tools/correct_corpus.py --source test-corpus/held-out --out build/held-out-corrected

`test-corpus/animal-pet-20` is integrity locked. This only ever reads from a
source directory and only ever writes to the output directory.

The summary beside the output answers the other question in the same run: a
paragraph the model refused still comes back repaired by the deterministic
rules, and from the outside that is indistinguishable from a full correction.
``rules_only`` counts them and ``reasons`` ranks the guards responsible.

**Measure on an idle machine.** The policy pauses the local AI below 4 GB free
RAM, so a run competing with the test suite measures the machine rather than the
product. The giveaway is paragraphs completing in under a second.
"""
from __future__ import annotations

import argparse
import collections
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT / "src", ROOT / "tools"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from echo_link_core.polish import PolishEngine  # noqa: E402
from echo_link_core.polish_error_score import error_score  # noqa: E402
from echo_link_core.polish_types import (  # noqa: E402
    CorrectionRequest,
    PolishMode,
    PolishTask,
)
from grade_corpus import essay_documents, paragraphs  # noqa: E402


def correct(engine: PolishEngine, text: str) -> tuple[str, bool, str]:
    """The corrected paragraph, whether the model produced it, and why not."""
    request = CorrectionRequest(
        text=text,
        task=PolishTask.PROOFREAD,
        mode=PolishMode("balanced"),
        command="Proofread",
        download_consent=True,
        manual=True,
    )
    try:
        result = engine.analyze(request)
    except Exception as exc:  # noqa: BLE001 - a crash is a result, and it is recorded
        return text, False, f"crashed: {type(exc).__name__}: {exc}"[:200]
    if result.used_llm:
        return str(result.text), True, ""
    # The user-facing message says only "not accepted". The cause is the guard,
    # so record that: a message cannot tell anyone which one to look at.
    reason = str(result.metrics.get("refusal_reason") or "").strip()
    reason = reason or "; ".join(result.validation.failures) or "no reason recorded"
    return str(result.text), False, reason


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--label", default="")
    arguments = parser.parse_args()

    source = ROOT / arguments.source
    destination = ROOT / arguments.out
    documents = essay_documents(source)
    if not documents:
        print(f"no documents in {source}", file=sys.stderr)  # noqa: T201
        return 1
    destination.mkdir(parents=True, exist_ok=True)

    engine = PolishEngine()
    reasons: collections.Counter[str] = collections.Counter()
    rows: list[dict[str, object]] = []
    started = time.monotonic()
    total = corrected_by_model = 0

    for path in documents:
        body = paragraphs(path)
        repaired: list[str] = []
        model_count = 0
        before = after = 0
        for index, paragraph in enumerate(body, start=1):
            begin = time.monotonic()
            text, used_model, reason = correct(engine, paragraph)
            repaired.append(text)
            total += 1
            model_count += used_model
            corrected_by_model += used_model
            before += error_score(paragraph)
            after += error_score(text)
            if not used_model:
                reasons[reason[:120]] += 1
            print(  # noqa: T201
                f"{path.stem[:34]} p{index}: model={used_model} "
                f"{round(time.monotonic() - begin, 2)}s {reason[:80]}",
                flush=True,
            )
        (destination / f"{path.stem}.txt").write_text(
            "\n".join(repaired) + "\n", encoding="utf-8", newline="\n"
        )
        rows.append(
            {
                "name": path.stem,
                "paragraphs": len(body),
                "corrected_by_model": model_count,
                "rules_only": len(body) - model_count,
                "errors_before": before,
                "errors_after": after,
            }
        )

    label = f"-{arguments.label}" if arguments.label else ""
    summary = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "source": str(source.relative_to(ROOT)),
        "out": str(destination.relative_to(ROOT)),
        "paragraphs": total,
        "corrected_by_model": corrected_by_model,
        "rules_only": total - corrected_by_model,
        "total_seconds": round(time.monotonic() - started, 1),
        "reasons": [
            {"count": count, "reason": reason} for reason, count in reasons.most_common()
        ],
        "essays": rows,
    }
    report = ROOT / "build" / f"corrected{label}.json"
    report.write_text(json.dumps(summary, indent=2), encoding="utf-8", newline="\n")

    print(  # noqa: T201
        f"\n{corrected_by_model} of {total} paragraphs corrected by the model, "
        f"{total - corrected_by_model} rules-only, in {summary['total_seconds']}s"
    )
    for entry in summary["reasons"]:
        print(f"  {entry['count']:>3}  {entry['reason']}")  # noqa: T201
    print(f"\nwrote {destination} and {report}")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
