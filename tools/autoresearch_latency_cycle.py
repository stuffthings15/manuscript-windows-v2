"""One AutoResearch experiment measured in SECONDS PER SECTION, lower is better.

    .venv/Scripts/python.exe tools/autoresearch_latency_cycle.py \
        --describe "stop keeping the fallback pack resident"

Prints one line: `KEEP 11.2`, `DISCARD 15.8` or `CRASH dead`.

Curtis reported corrections taking significantly longer. Timing the pieces ruled
out the speller - the whole path is under 2ms - and the harness shows per-essay
times flat all day at roughly 13-15 seconds per section. That is not a
regression, but it is slow for someone waiting on one sentence, and until now
nothing in this loop could measure it: `autoresearch_cycle` scores correction
QUALITY and would report a change that halved the wait as a flat discard.

The metric is the median seconds per applied section on a plain proofread run.
Median rather than mean because one cold model load - the ExecutionPlan allows 90
seconds for it - would otherwise swamp twenty normal sections and make every
experiment look like noise.

LOWER IS BETTER, and that is not a detail. `decide` is asked explicitly with
`higher_is_better=False`; the default rule would keep the slowest result every
time and record it as an improvement, with every row in results.tsv reading
"keep" and nothing in the log looking wrong.

No tone. This measures what a writer waits for when they press the hotkey, and a
tone request is a different, slower thing that few sections ask for.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import autoresearch_cycle  # noqa: E402
import autoresearch_status  # noqa: E402

CORPUS = ROOT / "test-corpus" / "error-riddled-20-docx"
SUMMARY = ROOT / "build" / "batch-summary.json"

# Three essays, so a single slow one cannot decide the number, and the same
# slice every time so results stay comparable.
FIRST_ESSAY = 1
LAST_ESSAY = 3
SLICE = "1-3"


def _seconds_per_section() -> tuple[float | None, str]:
    """Median seconds per applied section across the runs just measured."""
    try:
        rows = json.loads(SUMMARY.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"no readable batch summary ({type(exc).__name__})"
    rates = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        presses = row.get("presses") or 0
        seconds = row.get("seconds") or 0.0
        if row.get("error") or presses <= 0:
            # A run that failed says nothing about speed. Averaging it in would
            # turn a crash into a fast or slow number depending only on how
            # early it died.
            continue
        rates.append(float(seconds) / float(presses))
    if not rates:
        return None, "no essay run completed, so nothing was timed"
    return statistics.median(rates), ""


def measure(log: Path) -> tuple[float | None, str]:
    """(median seconds per section, reason it is missing). Never both.

    Restarts the app and checks the build it published first, for the reason
    `autoresearch_run` carries the same guard: an experiment measured against
    the previously loaded code returns the baseline and looks entirely
    reasonable. That matters even more here, because most latency levers live in
    the ExecutionPlan and are read once when the app starts.
    """
    import autoresearch_run

    autoresearch_run._restart_word()
    subprocess.run(
        [sys.executable, "-u", str(ROOT / "tools/restart_app_and_wait.py")],
        cwd=ROOT, capture_output=True, text=True, timeout=600,
    )
    stale = autoresearch_run._stale_reason()
    if stale:
        return None, f"stale app, {stale}"

    with log.open("w", encoding="utf-8") as stream:
        subprocess.run(
            [
                sys.executable, str(ROOT / "tools/run_essays_batch.py"),
                "--from", str(FIRST_ESSAY), "--to", str(LAST_ESSAY),
                "--corpus", str(CORPUS), "--as-a-writer",
            ],
            cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, timeout=5400,
        )
    return _seconds_per_section()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--describe", required=True)
    parser.add_argument(
        "--baseline-only",
        action="store_true",
        help="measure and record without committing an experiment",
    )
    arguments = parser.parse_args(argv)

    tag = autoresearch_cycle.slice_tag(SLICE, kind="latency")
    description = f"{tag} {arguments.describe}"
    before = autoresearch_cycle._git("rev-parse", "--short", "HEAD")

    if not arguments.baseline_only:
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
        autoresearch_cycle._git("commit", "-q", "-m", f"latency experiment: {description}")

    commit = autoresearch_cycle._git("rev-parse", "--short", "HEAD")
    if not arguments.baseline_only and commit == before:
        print("NOCHANGE nothing was edited")  # noqa: T201
        return 4

    baseline = autoresearch_cycle.current_baseline(SLICE, kind="latency")
    autoresearch_status.write_status(
        {
            "phase": "latency",
            "description": arguments.describe,
            "essays": SLICE,
            "experiment": commit,
            "baseline": f"{baseline:.1f}s" if baseline else "-",
        }
    )
    try:
        seconds, reason = measure(ROOT / "latency-run.log")
    finally:
        autoresearch_status.clear_status()

    status = autoresearch_cycle.decide(baseline, seconds, higher_is_better=False)
    autoresearch_cycle.record(commit, seconds, 0.0, status, description)
    if status != "keep" and not arguments.baseline_only:
        autoresearch_cycle._git("reset", "--hard", before)

    if seconds is None:
        print(f"CRASH {reason}")  # noqa: T201
    else:
        print(f"{status.upper()} {seconds:.1f}  (baseline {baseline:.1f})")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
