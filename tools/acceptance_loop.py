"""Run the acceptance tiers back to back until Manuscript is fit to ship.

    .venv/Scripts/python.exe tools/acceptance_loop.py
    .venv/Scripts/python.exe tools/acceptance_loop.py --iterations 3
    .venv/Scripts/python.exe tools/acceptance_loop.py --status

This drives `tools/release_acceptance.py` and nothing else. It does not grade,
judge, or touch Word itself - if a number here disagreed with the tool that
produced it, this would be the one that was wrong.

What it adds over running the tier by hand is the part that kept going wrong
while a person was watching:

**It refuses to run twice at once.** Two harnesses on one app produced hours of
false evidence on 2026-08-23 - a run reporting the tone control was unreachable
and the app was stuck on Friendly, both untrue, both caused by a second harness
driving the same window. `TaskStop` stops a task and not its children, so the
first sweep was still running when its replacement started. A lockfile carrying
a live pid is the cheap end of that problem.

**It never overwrites evidence.** The sweep script that produced those false
readings wrote `batch-<TONE>.txt` fresh on every attempt, so attempt 3 destroyed
what attempts 1 and 2 had recorded, including the rows quoted in a report an
hour earlier. Every artefact here carries its iteration.

**It says what it decided and why.** One row per iteration in
`loop-log.jsonl`, and `loop-state.json` holds the current gate status so a
person - or the next session - can read where it is without parsing a console
log that may already have scrolled away.

The stopping rule is TWO consecutive green full tiers, not one. A Windows file
lock failed `test_report_write_is_atomic` once on 2026-08-23 and did not
reproduce; one green run is not evidence that the product is green.

Thresholds live in `docs/RELEASE_ACCEPTANCE.md` and are not this tool's to
move. A loop permitted to edit the bar it is measured against can always
finish, and what it finishes is meaningless.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
EVIDENCE = ROOT / "build" / "acceptance"
LOCK = EVIDENCE / "loop.lock"
STATE = EVIDENCE / "loop-state.json"
LOG = EVIDENCE / "loop-log.jsonl"

# Two, because one is not evidence. See the module docstring.
GREEN_RUNS_TO_STOP = 2

# When the same gate fails this many times with no movement, the diagnosis is
# wrong and more iterations will not fix it. Stopping to say so beats burning
# the machine all night on a hypothesis that is already disproved.
STUCK_ITERATIONS = 3


def _say(message: str) -> None:
    print(message, flush=True)  # noqa: T201


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _pid_alive(pid: int) -> bool:
    """Whether a pid is running, without importing psutil.

    A stale lock left by a killed run must not block the loop for ever, and a
    live one must not be ignored.
    """
    if pid <= 0:
        return False
    try:
        found = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        # Cannot tell. Treat it as alive: refusing to start is recoverable in
        # a way that two harnesses on one app is not.
        return True
    return str(pid) in (found.stdout or "")


def take_lock() -> bool:
    """Claim the right to drive the app, or report who already has it."""
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    try:
        held = json.loads(LOCK.read_text(encoding="utf-8"))
        pid = int(held.get("pid") or 0)
    except (OSError, ValueError, TypeError):
        pid = 0
    if pid and pid != os.getpid() and _pid_alive(pid):
        _say(f"another acceptance loop is already running as pid {pid}.")
        _say(f"if that is wrong, delete {LOCK}")
        return False
    LOCK.write_text(
        json.dumps({"pid": os.getpid(), "since": _stamp()}, indent=2),
        encoding="utf-8",
    )
    return True


def release_lock() -> None:
    try:
        LOCK.unlink()
    except OSError:
        pass


def run_tier(tier: str, iteration: int) -> dict[str, object]:
    """Run one acceptance tier and return its evidence.

    The evidence file is read back rather than the console being parsed. The
    tier writes what it decided; reading its own words is the only way this
    cannot drift from it.
    """
    _say(f"\n{'=' * 60}\niteration {iteration}: {tier} tier")
    started = time.time()
    transcript = EVIDENCE / f"loop-{iteration:03d}-{tier}.txt"
    with transcript.open("w", encoding="utf-8") as sink:
        completed = subprocess.run(
            [str(PYTHON), "-u", str(ROOT / "tools" / "release_acceptance.py"), f"--{tier}"],
            stdout=sink,
            stderr=subprocess.STDOUT,
            cwd=str(ROOT),
        )
    elapsed = round(time.time() - started, 1)

    evidence = EVIDENCE / f"acceptance-{tier}.json"
    try:
        results = json.loads(evidence.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # The tier died before writing. That is a failure with a reason, not a
        # missing measurement - and the transcript is where the reason is.
        return {
            "tier": tier,
            "passed": False,
            "failures": [
                f"the {tier} tier wrote no evidence file; exit={completed.returncode}. "
                f"transcript: {transcript.name}"
            ],
            "seconds": elapsed,
            "transcript": transcript.name,
        }
    # Keep the tier's own evidence under this iteration's name too, so a later
    # run cannot overwrite the file this row refers to.
    (EVIDENCE / f"loop-{iteration:03d}-{tier}.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    results["seconds"] = elapsed
    results["transcript"] = transcript.name
    return results


def summarise(results: dict[str, object]) -> str:
    """One line a person can act on."""
    failures = list(results.get("failures") or [])
    if not failures:
        return f"{results.get('tier')} tier PASSED every threshold"
    return f"{results.get('tier')} tier failed {len(failures)}: " + "; ".join(
        str(item) for item in failures[:3]
    )


def record(iteration: int, results: dict[str, object], state: dict[str, object]) -> None:
    row = {
        "stamp": _stamp(),
        "iteration": iteration,
        "tier": results.get("tier"),
        "passed": bool(results.get("passed")),
        "seconds": results.get("seconds"),
        "failures": results.get("failures") or [],
        "transcript": results.get("transcript"),
    }
    with LOG.open("a", encoding="utf-8") as sink:
        sink.write(json.dumps(row) + "\n")
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def fingerprint(results: dict[str, object]) -> str:
    """What failed, ignoring the numbers that move between runs.

    Two iterations that fail the same gate for the same reason are not
    progress, however different the mean looks.
    """
    failures = [str(item) for item in (results.get("failures") or [])]
    # Strip every number, not just the threshold. The measured value sits
    # BEFORE "is below" - "coherent mean 84.5 is below 95.0" - so splitting on
    # the phrase alone still leaves 84.5, and a mean creeping to 84.9 would
    # read as a different failure and reset the stuck counter for ever.
    return " | ".join(
        sorted(re.sub(r"[0-9]+(?:\.[0-9]+)?", "#", item) for item in failures)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--iterations",
        type=int,
        default=0,
        help="stop after this many iterations. 0 means run until green.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="print where the loop got to and exit, driving nothing",
    )
    arguments = parser.parse_args(argv)

    if arguments.status:
        try:
            _say(STATE.read_text(encoding="utf-8"))
        except OSError:
            _say("the loop has not recorded a state yet.")
        return 0

    if not take_lock():
        return 2

    green = 0
    iteration = 0
    seen: list[str] = []
    state: dict[str, object] = {}
    try:
        while True:
            iteration += 1
            if arguments.iterations and iteration > arguments.iterations:
                _say(f"\nstopping: {arguments.iterations} iteration(s) requested.")
                return 1

            results = run_tier("fast", iteration)
            if results.get("passed"):
                # Fast is the cheap filter, not the gate. Only the full tier
                # can say the product is ready.
                results = run_tier("full", iteration)

            green = green + 1 if results.get("passed") and results.get("tier") == "full" else 0
            mark = fingerprint(results)
            seen.append(mark)
            stuck = (
                len(seen) >= STUCK_ITERATIONS
                and mark
                and len(set(seen[-STUCK_ITERATIONS:])) == 1
            )
            state = {
                "stamp": _stamp(),
                "iteration": iteration,
                "last_tier": results.get("tier"),
                "passed": bool(results.get("passed")),
                "consecutive_green_full": green,
                "failures": results.get("failures") or [],
                "next": (
                    "stop - ready"
                    if green >= GREEN_RUNS_TO_STOP
                    else "stop - the same gate failed with no movement"
                    if stuck
                    else "fix the failures above, then run again"
                ),
            }
            record(iteration, results, state)
            _say(summarise(results))

            if green >= GREEN_RUNS_TO_STOP:
                _say(
                    f"\nREADY: the full tier passed every threshold "
                    f"{green} times in a row."
                )
                return 0
            if stuck:
                _say(
                    f"\nSTOPPING: the same gate has failed {STUCK_ITERATIONS} "
                    "iterations running with no movement, so the diagnosis is "
                    "wrong and more iterations will not fix it:\n  " + mark
                )
                return 1
    finally:
        release_lock()


if __name__ == "__main__":
    raise SystemExit(main())
