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


def slice_tag(name: str, kind: str = "essays") -> str:
    """The prefix every description carries, naming the slice it was measured on.

    A score means nothing without the essays it came from, and results.tsv is
    fixed at the specification's five columns, so the slice goes in the one
    free-text field there is.

    Without this the loop silently breaks the moment the slice changes. Essay 8
    finished at 98 and essay 10 baselines at 94, so a flat "best kept score"
    baseline would have set an unreachable bar for every essay 10 experiment and
    discarded correct improvements as failures - indistinguishable, in the log,
    from having no ideas.
    """
    # `kind` because a slice is not always a set of essays. The tone cycle
    # measures a judge percentage on one tone, and tagging its rows
    # "essays Shorten:" would be a lie in the one field that says what a
    # number means. Defaulted so every existing caller and every row already
    # written keeps its exact shape.
    return f"{kind} {name}:"


def current_baseline(name: str, kind: str = "essays") -> float:
    """The best score kept so far ON THIS SLICE, which an experiment must beat."""
    tag = slice_tag(name, kind)
    best = 0.0
    try:
        for line in RESULTS.read_text(encoding="utf-8").splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) >= 5 and parts[3] == "keep" and parts[4].startswith(tag):
                try:
                    best = max(best, float(parts[1]))
                except ValueError:
                    continue
    except OSError:
        return 0.0
    return best


def missing_baseline(name: str, kind: str, *, higher_is_better: bool = True) -> str:
    """Why this slice cannot be experimented on yet, or "" if it can.

    The queue has refused a slice with no baseline since essays 4 and 6 were
    judged against 0.00 and escaped being wrong only because both happened to
    score 100. The CYCLES never had that guard, so calling one directly on a
    fresh slice keeps whatever it scores and writes a bar nothing supports -
    which every later comparison on that slice then inherits.

    Only for higher-is-better metrics. For a lower-is-better metric a zero
    baseline is deliberately "no bar", because zero is not a floor to beat but
    an impossible one; `decide` documents that.
    """
    if not higher_is_better:
        return ""
    if current_baseline(name, kind) > 0.0:
        return ""
    return (
        f"no baseline for {kind} {name}. Measure it unchanged and record the "
        f"row before running experiments on this slice."
    )


def decide(
    baseline: float, score: float | None, *, higher_is_better: bool = True
) -> str:
    """keep, discard or crash. The whole of steps 6, 8 and 9.

    Separated from the running so it can be tested without a Word session, and
    because this is the rule the loop turns on: if a no-op can be kept, the loop
    is decorative.
    """
    if score is None:
        return "crash"
    if higher_is_better:
        return "keep" if score > baseline else "discard"
    # LOWER is better for latency, and this direction is not optional. A
    # latency experiment judged by the default rule would keep the SLOWEST
    # result every time and record it as an improvement - a loop that
    # confidently optimises backwards while every row says "keep".
    #
    # A baseline of 0.0 means no measurement yet. For a lower-is-better metric
    # that is not a floor to beat but an impossible one, so it is treated as
    # "no bar" rather than silently discarding every result.
    if baseline <= 0.0:
        return "keep"
    return "keep" if score < baseline else "discard"


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


def void_row(commit: str, name: str, kind: str, score: float, reason: str) -> int:
    """Retire a kept row that should never have set a bar. Returns rows changed.

    `current_baseline` takes the MAX of kept scores on a slice, deliberately: the
    bar is the best result kept so far, a ratchet, and taking the most recent
    instead would let a regression lower it. That is right, and it is also why a
    single wrong `keep` poisons a slice permanently - the max never comes back
    down.

    Measured 2026-09-01. The first reliability baseline recorded 100.0 on a batch
    where one document had visibly failed, because the gate scored a stated stop
    reason with no section marker as complete. The corrected 80.0 row was
    appended below it and the bar stayed at 100.0, so the next experiment was
    judged against a number nothing supported. It had to be fixed by hand-editing
    results.tsv, which is not a thing the loop should ever require.

    `decide` writes only keep, discard and crash, so `void` existed as a hand
    annotation and nothing else. It is now writable, with the reason recorded in
    the description where a reader will see it.

    The row is rewritten in place rather than appended to, because an append
    cannot lower a max. That is the one edit this loop makes to its own ledger,
    and it is deliberately narrow: it changes a status and a description, never a
    score, and it refuses unless it can name exactly ONE row.

    The score is part of the identity for that reason. A commit and a slice are
    not unique - the 2026-09-01 case had the wrong 100.0 and the corrected 80.0
    on the SAME commit and the SAME slice, and a first version of this matched
    both and would have voided the good row along with the bad one. Its own test
    caught that. Returning 0 rather than voiding several is the safe direction:
    a bar left too high wastes experiments, a corrected row silently retired
    loses the measurement.
    """
    tag = slice_tag(name, kind)
    printed = f"{score:.6f}"
    try:
        lines = RESULTS.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    matches = [
        index
        for index, line in enumerate(lines)
        for parts in [line.split("\t")]
        if len(parts) >= 5
        and parts[0] == commit
        and parts[1] == printed
        and parts[3] == "keep"
        and parts[4].startswith(tag)
    ]
    if len(matches) != 1:
        return 0
    parts = lines[matches[0]].split("\t")
    parts[3] = "void"
    parts[4] = f"{parts[4]}, VOID: {reason}"
    lines[matches[0]] = "\t".join(parts)
    RESULTS.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 1


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

    baseline = current_baseline(arguments.essays)
    status = decide(baseline, score)
    record(
        commit,
        score,
        memory_gb,
        status,
        f"{slice_tag(arguments.essays)} {arguments.describe}",
    )

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
