"""Record a per-class baseline for one slice, from ONE measurement.

WHAT THIS CANNOT DO, first, because the plan it came from claimed otherwise.

It cannot un-saturate a slice. `grade_corpus` returns
`total = round(SCALE * (spelling + homophones + punctuation + grammar +
coherence))`, so the total IS the sum of the five classes and a slice at 100.0
has every class at its own maximum by arithmetic. The 2026-09-01 plan said "a
slice at 100 may still have real room on grammar" and that is false for this
rubric. Measured on the 20 essay corpus at 99.9 on 2026-09-08:

    spelling     17.647 / 17.647     room 0.000
    homophones   17.647 / 17.647     room 0.000
    punctuation  23.529 / 23.529     room 0.000
    grammar      23.412 / 23.529     room 0.118
    coherence    17.647 / 17.647     room 0.000  (never penalised at all)

That 0.118 is the entire remaining headroom of the essay corpus.

WHAT IT IS FOR. A slice that is NOT saturated has its shortfall in some class,
and the total does not say which. Essay 17 sits at 98.0 and the two missing
points are in one or two terms; an experiment aimed at the wrong one measures
flat however good it is. This says where to aim.

The second reason is the one `autoresearch_cycle` already gives: an experiment
that fixes punctuation while breaking grammar measures flat on the total and is
recorded as a bad idea. Per-class rows make that visible.

The plumbing for all of this already exists and has never been used.
`autoresearch_cycle.py --metric grammar` judges one term against its own
baseline kind, and `autoresearch_queue.py` dispatches `essays-<class>` entries.
What was missing is only the baseline rows: `current_baseline` reads kept rows,
and no row on `essays-grammar 8:` has ever been written.

Writing them one at a time through the cycle would measure the same corpus five
times over. `autoresearch_run.py` already prints all five class means from a
single pass, so this runs the slice once and records one row per class.

A CRASH IS NOT A ZERO. If the run does not produce a score, this records crash
rows for every class rather than five zeroes, because a zero baseline is worse
than a missing one - it puts a bar in results.tsv that nothing supports and the
next result is kept whatever it scores.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import autoresearch_cycle  # noqa: E402

CLASSES = ("spelling", "homophones", "punctuation", "grammar", "coherence")

# The run refuses and returns this when somebody is at the keyboard. It is not a
# failed measurement and must not be recorded as one.
_STOPPED_FOR_IDLE = 2


def _measure(essays: str, corpus: str | None, require_idle: float) -> tuple[str, int]:
    command = [
        sys.executable, "-u", str(ROOT / "tools/autoresearch_run.py"),
        "--essays", essays,
        "--require-idle", str(require_idle),
        "--describe", "per-class baseline",
    ]
    if corpus:
        command += ["--corpus", corpus]
    done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    return (done.stdout or "") + (done.stderr or ""), done.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--essays", required=True, help="the slice to baseline")
    parser.add_argument("--corpus", default="")
    parser.add_argument("--require-idle", type=float, default=300.0)
    parser.add_argument(
        "--describe",
        default="per-class baseline",
        help="what this measurement is, for the ledger's one free-text field",
    )
    arguments = parser.parse_args(argv)

    text, code = _measure(arguments.essays, arguments.corpus, arguments.require_idle)
    if code == _STOPPED_FOR_IDLE:
        print("SKIPPED someone is at the keyboard", flush=True)  # noqa: T201
        return _STOPPED_FOR_IDLE

    commit = autoresearch_cycle._git("rev-parse", "--short", "HEAD")
    vram = re.search(r"^peak_vram_mb: +([0-9.]+)", text, re.MULTILINE)
    memory_gb = (float(vram.group(1)) / 1024.0) if vram else 0.0

    written = 0
    for name in CLASSES:
        found = re.search(rf"^class_{name}: +([0-9.]+)", text, re.MULTILINE)
        score = float(found.group(1)) if found else None
        # keep, so current_baseline can see it - that function reads kept rows
        # only, and a baseline that no experiment can be judged against is not
        # a baseline.
        status = "keep" if score is not None else "crash"
        autoresearch_cycle.record(
            commit,
            score,
            memory_gb,
            status,
            f"{autoresearch_cycle.slice_tag(arguments.essays, f'essays-{name}')} "
            f"{arguments.describe}",
        )
        written += 1
        if score is None:
            print(f"CRASH {name}: no class line in the run output")  # noqa: T201
        else:
            print(f"BASELINE {name}: {score:.2f}")  # noqa: T201

    if written and all(
        re.search(rf"^class_{name}: ", text, re.MULTILINE) is None for name in CLASSES
    ):
        reason = re.search(r"^crash: +(.+)", text, re.MULTILINE)
        print(  # noqa: T201
            f"CRASH {reason.group(1).strip() if reason else 'the run produced no classes'}"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
