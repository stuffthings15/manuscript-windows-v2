"""Ask one question of the whole corpus: did the model correct this paragraph?

The product promise is that every section a writer is shown has been
corrected. A section the AI refused is still shown, still says Accept, and
still reports success - it just hands back deterministic rules output, which
on this corpus leaves most of the errors on screen. That is the defect this
probe measures, one paragraph at a time, with the reason for every refusal.

    $env:PYTHONPATH = "src"
    .venv/Scripts/python.exe tools/model_coverage_probe.py
    .venv/Scripts/python.exe tools/model_coverage_probe.py --essays 1,2 --label e12

Writes build/model-coverage[-label].json. Read the `reasons` block first: it
ranks the causes, and the top cause is the next thing to fix.
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

from animal_essay_corpus_probe import CORPUS, load_essays  # noqa: E402
from echo_link_core.polish import PolishEngine  # noqa: E402
from echo_link_core.polish_error_score import error_score  # noqa: E402
from echo_link_core.polish_types import (  # noqa: E402
    CorrectionRequest,
    PolishMode,
    PolishTask,
)


def paragraphs(text: str) -> list[str]:
    return [line.strip() for line in str(text).split("\n") if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--essays", default="")
    parser.add_argument("--label", default="")
    parser.add_argument("--paragraphs", type=int, default=0)
    arguments = parser.parse_args()

    wanted = {
        int(piece)
        for piece in arguments.essays.replace(" ", "").split(",")
        if piece.isdigit()
    }
    essays = load_essays(CORPUS)
    engine = PolishEngine()
    rows: list[dict[str, object]] = []
    reasons: collections.Counter[str] = collections.Counter()
    started = time.monotonic()

    for number, essay in enumerate(essays, start=1):
        if wanted and number not in wanted:
            continue
        name = str(essay.get("name") or essay.get("id") or f"essay-{number}")
        pieces = paragraphs(str(essay["text"]))
        if arguments.paragraphs:
            pieces = pieces[: arguments.paragraphs]
        for index, piece in enumerate(pieces, start=1):
            request = CorrectionRequest(
                text=piece,
                task=PolishTask.PROOFREAD,
                mode=PolishMode("balanced"),
                command="Proofread",
                download_consent=True,
                manual=True,
            )
            begin = time.monotonic()
            try:
                result = engine.analyze(request)
            except Exception as exc:  # noqa: BLE001 - a crash is a result too
                rows.append(
                    {
                        "essay": name,
                        "paragraph": index,
                        "used_llm": False,
                        "reason": f"crashed: {type(exc).__name__}: {exc}"[:200],
                        "seconds": round(time.monotonic() - begin, 3),
                    }
                )
                reasons[f"crashed: {type(exc).__name__}"] += 1
                continue
            # The user-facing message says only "not accepted". The cause is in
            # the validation failures, so rank on those - a message cannot tell
            # anyone which guard to look at.
            failures = tuple(result.validation.failures)
            engine_reason = str(result.metrics.get("refusal_reason") or "").strip()
            reason = ""
            if not result.used_llm:
                reason = engine_reason or "; ".join(failures) or (
                    f"no message (backend={result.plan.backend.value} "
                    f"pack={result.plan.pack_id or 'rules-only'} "
                    f"use_llm={result.plan.use_llm})"
                )
                reasons[reason[:120]] += 1
            rows.append(
                {
                    "essay": name,
                    "paragraph": index,
                    "used_llm": bool(result.used_llm),
                    "rejected_llm": bool(result.rejected_llm),
                    "retries": int(result.retries),
                    "reason": reason[:300],
                    "message": str(result.message)[:200],
                    "validation_failures": list(failures),
                    "residual_errors": list(result.residual_errors),
                    "errors_before": error_score(piece),
                    "errors_after": error_score(result.text),
                    "seconds": round(time.monotonic() - begin, 3),
                }
            )
            print(
                f"{name} p{index}: llm={result.used_llm} "
                f"{rows[-1]['errors_before']}->{rows[-1]['errors_after']} "
                f"{rows[-1]['seconds']}s {reason[:90]}",
                flush=True,
            )

    corrected = sum(1 for row in rows if row["used_llm"])
    report = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "paragraphs": len(rows),
        "paragraphs_corrected_by_model": corrected,
        "paragraphs_rules_only": len(rows) - corrected,
        "errors_before": sum(int(row.get("errors_before") or 0) for row in rows),
        "errors_after": sum(int(row.get("errors_after") or 0) for row in rows),
        "total_seconds": round(time.monotonic() - started, 1),
        "reasons": [
            {"count": count, "reason": reason}
            for reason, count in reasons.most_common()
        ],
        "rows": rows,
    }
    suffix = f"-{arguments.label}" if arguments.label else ""
    output = ROOT / "build" / f"model-coverage{suffix}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print()
    print(
        f"{corrected} of {len(rows)} paragraphs corrected by the model; "
        f"errors {report['errors_before']} -> {report['errors_after']}"
    )
    for entry in report["reasons"]:
        print(f"  {entry['count']:4d}  {entry['reason']}")
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
