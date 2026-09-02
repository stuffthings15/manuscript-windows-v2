"""One whole AutoResearch experiment for RELIABILITY, from commit to keep-or-reset.

    .venv/Scripts/python.exe tools/autoresearch_reliability_cycle.py \
        --slice shapes --describe "pin the pack so the planner cannot downgrade"

Prints exactly one line: `KEEP 100.0`, `DISCARD 60.0` or `CRASH dead`.

WHY THIS EXISTS. The loop could only ever measure correction QUALITY, so it was
structurally blind to the thing that wasted the most machine time: of 55 logged
experiments, 11 produced no information at all - pack downgrades, a nine hour HUD
hang, harness faults. One experiment in five told us nothing, and the metric
could not see it.

The metric here is COMPLETION RATE: the share of documents whose Word run
actually finished, from the same `build/batch-summary.json` the release gate
reads, using the same `_run_was_truncated` rule so the two cannot drift on what
"finished" means. Higher is better, like the corpus score.

WHY THIS CORPUS. `test-corpus/document-shapes` carries the structures that broke
a real assignment on 2026-09-01 - automatic numbering, automatic bullets, two
numbering levels, trailing blank paragraphs, a non-breaking space - and which
the gated corpus has never contained. That corpus has no scoring ground truth,
so it cannot feed a quality metric, but completion needs no ground truth: either
the run finished or it did not. The two fit together exactly.

WHAT THIS DOES NOT DO: score the corrections. A reliability experiment that
raises completion by making the corrections worse is a bad trade, and this
cannot see that, which is why the plan requires the corpus score to be rechecked
before any reliability change is kept for good. Recorded here rather than
enforced, because the check belongs to the merge and not to the experiment.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import autoresearch_cycle  # noqa: E402
import autoresearch_status  # noqa: E402

CORPUS = ROOT / "test-corpus" / "document-shapes"
SUMMARY = ROOT / "build" / "batch-summary.json"

# The pack a measurement must be served by, end to end. Anything smaller is a
# different product answering, which is the lesson the tone cycle paid for.
REQUIRED_PACK = "gemma4-12b-qat-v1"


def _documents() -> list[Path]:
    return sorted(CORPUS.glob("*.docx"))


def completion_rate() -> tuple[float | None, str]:
    """(percentage of documents whose run finished, reason it is missing).

    Reads the batch summary the gate reads, and calls a run finished by the
    gate's own `_run_was_truncated`. A second opinion on what "finished" means
    is a second thing that can rot.
    """
    import release_acceptance

    try:
        rows = json.loads(SUMMARY.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"the batch wrote no readable summary ({type(exc).__name__})"
    if not isinstance(rows, list) or not rows:
        return None, "the batch summary held no runs"
    finished = sum(
        1
        for row in rows
        if isinstance(row, dict) and not release_acceptance._run_was_truncated(row)
    )
    return 100.0 * finished / len(rows), ""


def measure(log: Path) -> tuple[float | None, str]:
    """(completion rate, reason it is missing). Never both.

    Restarts Word and the app first and refuses a stale build, for the reason
    `autoresearch_run` records and paid for three times in two days: a run that
    measures a process which is not the build under test is worse than no run,
    because it produces a number.
    """
    import autoresearch_run

    documents = _documents()
    if not documents:
        return None, f"no documents under {CORPUS}"

    autoresearch_run._restart_word()
    subprocess.run(
        [sys.executable, "-u", str(ROOT / "tools/restart_app_and_wait.py")],
        cwd=ROOT, capture_output=True, text=True, timeout=600,
    )
    stale = autoresearch_run._stale_reason()
    if stale:
        return None, f"stale app, {stale}"

    # Refused at prewarm rather than after driving Word for half an hour. The
    # tone cycle learned this the expensive way on 2026-09-01: five of six
    # baselines were thrown away AFTER the runs, and two hours of measurement
    # produced one number.
    mark = autoresearch_run._log_length()
    prewarmed = autoresearch_run._served_pack(0)[0]
    if prewarmed not in ("unknown", REQUIRED_PACK):
        return None, (
            f"the app prewarmed {prewarmed}, not {REQUIRED_PACK}; "
            f"free the GPU before measuring reliability"
        )

    SUMMARY.unlink(missing_ok=True)
    with log.open("w", encoding="utf-8") as stream:
        subprocess.run(
            [
                sys.executable, str(ROOT / "tools/run_essays_batch.py"),
                "--from", "1", "--to", str(len(documents)),
                "--corpus", str(CORPUS), "--as-a-writer",
            ],
            cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, timeout=7200,
        )

    # A run two packs served is not a measurement of either. Same rule as tone,
    # and for the same reason: a smaller pack fails the meaning guards, a failed
    # guard falls back to rules, and the score then tracks which sections
    # happened to be handed to the small model.
    packs = autoresearch_run._packs_that_served(mark)
    if len(packs) > 1:
        return None, f"more than one pack served this run: {sorted(packs)}"
    return completion_rate()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--slice", default="shapes")
    parser.add_argument("--describe", required=True, help="what this experiment tries")
    arguments = parser.parse_args(argv)

    tag = autoresearch_cycle.slice_tag(arguments.slice, kind="reliability")
    description = f"{tag} {arguments.describe}"
    before = autoresearch_cycle._git("rev-parse", "--short", "HEAD")

    lint = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "src", "tools"],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
    )
    if lint.returncode != 0:
        tail = lint.stdout.strip().splitlines()[-1] if lint.stdout else "failed"
        print(f"LINT {tail}")  # noqa: T201
        return 3

    autoresearch_cycle._git("add", "-A", "src", "tools")
    autoresearch_cycle._git("commit", "-q", "-m", f"reliability experiment: {description}")
    commit = autoresearch_cycle._git("rev-parse", "--short", "HEAD")
    if commit == before:
        print("NOCHANGE nothing was edited")  # noqa: T201
        return 4

    baseline = autoresearch_cycle.current_baseline(arguments.slice, kind="reliability")
    autoresearch_status.write_status(
        {
            "phase": f"reliability {arguments.slice}",
            "description": arguments.describe,
            "essays": f"1-{len(_documents())}",
            "experiment": commit,
            "baseline": f"{baseline:.1f}",
        }
    )
    try:
        rate, reason = measure(ROOT / f"reliability-run-{arguments.slice}.log")
    finally:
        autoresearch_status.clear_status()

    status = autoresearch_cycle.decide(baseline, rate)
    autoresearch_cycle.record(commit, rate, 0.0, status, description)
    if status != "keep":
        autoresearch_cycle._git("reset", "--hard", before)

    if rate is None:
        print(f"CRASH {reason}")  # noqa: T201
    else:
        print(f"{status.upper()} {rate:.1f}  (baseline {baseline:.1f})")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
