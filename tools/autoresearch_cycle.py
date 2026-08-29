"""One whole AutoResearch experiment, from commit to keep-or-reset, in one call.

    .venv/Scripts/python.exe tools/autoresearch_cycle.py --describe "raise the tone temperature"

Prints exactly one line: `KEEP 94.5`, `DISCARD 94.0` or `CRASH dead`.

This exists because of a specific failure. The specification's loop is nine
steps, and step 4 is a single command; ours had become edit, twelve-minute test
suite, fix, twelve-minute suite, commit, twelve-minute Word run, report. Every
one of those was a turn, and every turn was a place where Curtis had to prompt
to get the next one. He said he should not have to keep prompting and he was
right: an autonomous loop that needs a human between its phases is not
autonomous, whatever the log says.

So everything after the code edit lives here. The agent's turn becomes two tool
calls - make the change, run this - which is what "12 experiments an hour,
unattended" actually requires.

Steps 3 and 5 through 9 of the specification, in order:

    3. git commit
    4. run the experiment          (tools/autoresearch_run.py)
    5. read one line out of it
    6. no score means a crash
    7. record it in results.tsv
    8. improved -> advance the branch
    9. otherwise -> git reset --hard back to where we started

WHAT THIS DELIBERATELY DOES NOT DO: run the test suite. The specification's only
bar inside the loop is that the code runs, and `program.md` already says the
suite gates a merge rather than an experiment. Running it per experiment was a
self-inflicted twelve-minute tax that made the budget impossible. Nothing
reaches master without the full suite and the six release gates; that gate moved,
it did not disappear. ruff still runs here, because seconds are affordable and a
syntax error wastes a whole experiment.

The reset in step 9 is `--hard`, which is safe here in a way it is not
elsewhere: the commit happens FIRST, so the discarded work is always recoverable
by the hash recorded in results.tsv, and it only ever runs on the
autoresearch branch.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

# The score to beat. Anything equal or worse is discarded, because the measured
# run-to-run spread of this metric is 0.00 - three unchanged baselines returned
# 91.500000 exactly. Upstream needs a threshold because val_bpb wanders; this
# does not, so strict improvement is the honest rule.
RESULTS = ROOT / "results.tsv"


def _git(*arguments: str) -> str:
    done = subprocess.run(
        ["git", *arguments], cwd=ROOT, capture_output=True, text=True, timeout=120
    )
    return done.stdout.strip()


def current_baseline() -> float:
    """The best score kept so far, which is what a new experiment must beat."""
    best = 0.0
    try:
        for line in RESULTS.read_text(encoding="utf-8").splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) >= 4 and parts[3] == "keep":
                try:
                    best = max(best, float(parts[1]))
                except ValueError:
                    continue
    except OSError:
        return 0.0
    return best


def decide(baseline: float, score: float | None) -> str:
    """keep, discard or crash. The whole of steps 6, 8 and 9.

    Separated from the running so it can be tested without a Word session, and
    because this is the rule the loop turns on: if a no-op can be kept, the loop
    is decorative.
    """
    if score is None:
        return "crash"
    return "keep" if score > baseline else "discard"


def record(commit: str, score: float | None, memory_gb: float, status: str,
           description: str) -> None:
    """Append one row. Tab separated, five columns, per the specification.

    Commas are not used because descriptions contain them, and a comma
    separated file silently shifts every field after the first comma.
    """
    if not RESULTS.exists():
        RESULTS.write_text(
            "commit\tscore\tmemory_gb\tstatus\tdescription\n", encoding="utf-8"
        )
    # 0.000000 and 0.0 for a crash, so the file stays parseable.
    printed = f"{score:.6f}" if score is not None else "0.000000"
    with RESULTS.open("a", encoding="utf-8") as handle:
        handle.write(
            f"{commit}\t{printed}\t{memory_gb:.1f}\t{status}\t{description}\n"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--describe", required=True, help="what this experiment tries")
    parser.add_argument("--essays", default="8", help="the slice, one essay by default")
    arguments = parser.parse_args(argv)

    before = _git("rev-parse", "--short", "HEAD")

    lint = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "src", "tools"],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
    )
    if lint.returncode != 0:
        # Not a crash row: nothing was measured and nothing was committed, so
        # there is no experiment to record. The agent fixes it and re-runs.
        print(f"LINT {lint.stdout.strip().splitlines()[-1] if lint.stdout else 'failed'}")  # noqa: T201
        return 3

    _git("add", "-A", "src", "tools")
    _git("commit", "-q", "-m", f"experiment: {arguments.describe}")
    commit = _git("rev-parse", "--short", "HEAD")
    if commit == before:
        print("NOCHANGE nothing was edited")  # noqa: T201
        return 4

    log = ROOT / "run.log"
    subprocess.run(
        [sys.executable, str(ROOT / "tools/autoresearch_run.py"),
         "--essays", arguments.essays, "--describe", arguments.describe],
        cwd=ROOT, stdout=log.open("w", encoding="utf-8"), stderr=subprocess.STDOUT,
        timeout=1200,
    )
    text = log.read_text(encoding="utf-8", errors="replace")

    found = re.search(r"^score: +([0-9.]+)", text, re.MULTILINE)
    score = float(found.group(1)) if found else None
    vram = re.search(r"^peak_vram_mb: +([0-9.]+)", text, re.MULTILINE)
    memory_gb = (float(vram.group(1)) / 1024.0) if vram else 0.0

    baseline = current_baseline()
    status = decide(baseline, score)
    record(commit, score, memory_gb, status, arguments.describe)

    if status != "keep":
        # The commit is already made, so this is recoverable by the hash in
        # results.tsv. Only ever on the autoresearch branch.
        _git("reset", "--hard", before)

    if score is None:
        reason = re.search(r"^crash: +(.+)", text, re.MULTILINE)
        print(f"CRASH {reason.group(1).strip() if reason else 'no score'}")  # noqa: T201
    else:
        print(f"{status.upper()} {score:.2f}  (baseline {baseline:.2f})")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
