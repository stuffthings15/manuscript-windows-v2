"""Time the dictation refinement stage on each model pack.

    $env:PYTHONPATH = "src"
    .venv/Scripts/python.exe tools/dictation_refinement_bench.py \
        --model gemma4:e4b-it-qat --model qwen3:14b --model qwen3.8:27b

Dictation has three stages and a model pack only touches the third:

    1. voice detection   Silero VAD, bundled inside faster-whisper
    2. speech to text    faster-whisper base.en
    3. refinement        the shared Ollama pack   <- only this one

So comparing packs needs no audio. Stages 1 and 2 are byte-identical whatever
pack is selected, and feeding the same raw transcript to stage 3 isolates the
thing that actually changes.

This calls `OllamaRefiner.refine` - the product's own method, with the
product's own prompt and greedy options - and varies only the model. It is not
a reimplementation, and if it disagreed with the product the product would be
right.

**This measures latency, and damage. It is not a correction quality number.**
Correction quality is measured through Word and the HUD as a writer, per
docs/TESTING.md; a headless run scored a corpus at 62 on 2026-08-14 and was
reported as a product regression when the local AI had simply latched off.
What this can say honestly is how long a refinement takes and whether the
model mangled protected content, both of which are latency and safety
questions rather than quality ones.

Why it exists: Curtis reported dictation "taking over a minute, extremely
inconsistent", and separately asked to move the product to an 18 GB model that
cannot fit this machine's 16.4 GB of VRAM. Dictation refinement and document
correction share one pack (`correction.py` takes `lease.plan.model` from the
shared coordinator), so a bigger pack lands directly in the path he already
finds slow. This turns that from an argument into a number.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from echo_link_core.correction import OllamaRefiner  # noqa: E402

# Raw dictation, as faster-whisper hands it over: no punctuation, spoken
# commands already expanded by the deterministic stage, and the kind of
# content the guards protect - a name, a number, a path, an address.
CLIPS = (
    "we was ready but teh file wasnt saved",
    "send the report to maria on tuesday she needs it before the board meeting",
    "the invoice came to four thousand two hundred dollars and its due on the "
    "fifteenth of march",
    "email curtisl at ayzaroutreach dot org and copy the file from c colon "
    "backslash projects backslash manuscript",
    "i think we should probably rewrite the whole section because it doesnt "
    "read well and the client said it was unclear",
    "the meeting is not going ahead so dont book the room and tell everyone "
    "its cancelled",
)


def bench(model: str, endpoint: str, timeout: float) -> dict:
    refiner = OllamaRefiner(endpoint)
    timings: list[float] = []
    outputs: list[dict] = []
    failures: list[str] = []

    for clip in CLIPS:
        started = time.monotonic()
        try:
            result = refiner.refine(
                clip,
                model=model,
                protected_terms=(),
                timeout=timeout,
            )
            elapsed = time.monotonic() - started
            text = str(getattr(result, "text", "") or "")
        except Exception as exc:  # noqa: BLE001 - a pack that cannot run is a result
            elapsed = time.monotonic() - started
            failures.append(f"{type(exc).__name__}: {exc}")
            text = ""
        timings.append(elapsed)
        outputs.append(
            {
                "spoken": clip,
                "refined": text,
                "seconds": round(elapsed, 2),
                # A thinking model that leaks its reasoning into the text is a
                # correctness failure, not a style one. Qwen3 families emit
                # <think> blocks unless think=false is honoured, and the
                # product sends think=false - this is where that gets checked
                # against a real response rather than trusted.
                "leaked_thinking": "<think>" in text.casefold(),
                "empty": not text.strip(),
            }
        )

    return {
        "model": model,
        "clips": len(CLIPS),
        "seconds_mean": round(statistics.fmean(timings), 2),
        "seconds_slowest": round(max(timings), 2),
        "seconds_total": round(sum(timings), 2),
        "failures": failures,
        "leaked_thinking": sum(1 for row in outputs if row["leaked_thinking"]),
        "empty_results": sum(1 for row in outputs if row["empty"]),
        "outputs": outputs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", action="append", default=[], required=True)
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--json", type=Path, default=None)
    arguments = parser.parse_args(argv)

    reports = []
    for model in arguments.model:
        print(f"\n=== {model}", flush=True)  # noqa: T201
        report = bench(model, arguments.endpoint, arguments.timeout)
        reports.append(report)
        print(  # noqa: T201
            f"  mean {report['seconds_mean']}s  slowest {report['seconds_slowest']}s"
            f"  total {report['seconds_total']}s",
            flush=True,
        )
        if report["failures"]:
            print(f"  FAILURES {report['failures'][:2]}", flush=True)  # noqa: T201
        if report["leaked_thinking"]:
            print(  # noqa: T201
                f"  LEAKED THINKING in {report['leaked_thinking']} of "
                f"{report['clips']} clips - think=false is not being honoured",
                flush=True,
            )
        if report["empty_results"]:
            print(f"  EMPTY in {report['empty_results']} clips", flush=True)  # noqa: T201
        for row in report["outputs"][:2]:
            print(f"    {row['seconds']:>6}s  {row['refined'][:90]}", flush=True)  # noqa: T201

    if arguments.json:
        arguments.json.parent.mkdir(parents=True, exist_ok=True)
        arguments.json.write_text(json.dumps(reports, indent=2), encoding="utf-8")
        print(f"\nevidence: {arguments.json}", flush=True)  # noqa: T201

    if len(reports) > 1:
        print("\n--- dictation refinement, slowest first", flush=True)  # noqa: T201
        for report in sorted(reports, key=lambda item: -item["seconds_mean"]):
            print(  # noqa: T201
                f"  {report['model']:<24} {report['seconds_mean']:>7}s mean"
                f"  {report['seconds_slowest']:>7}s slowest",
                flush=True,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
