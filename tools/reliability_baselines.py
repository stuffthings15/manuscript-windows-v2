"""Measure the reliability slice unchanged and record the row experiments beat.

    .venv/Scripts/python.exe tools/reliability_baselines.py

Prints exactly one line: `BASELINE 100.0` or `CRASH dead`.

WHY THIS EXISTS, AND WHY IT IS IN THE REPOSITORY. On 2026-09-02 every baseline
measured that night went through a throwaway script in the temporary directory
that called `measure()` directly. Two things followed, and both were process
failures rather than bugs.

The status panel never appeared, because only the cycles' `main()` published it,
and Curtis noticed: "The auto research message in the top right-hand corner in
green is not available. It is not showing, and it should be showing if we are
conducting auto research." The panel is the promise that a run is real, and a
measurement that does not show it is indistinguishable from no measurement at
all.

And nothing measured that way was reproducible. A script outside the repository
cannot be reviewed, cannot be re-run by anyone else, and leaves a row in
results.tsv that nobody can check. `tone_baselines.py` is the shape this should
have followed from the start.

A BASELINE IS NOT AN EXPERIMENT. It measures the tree as it stands, commits
nothing, resets nothing, and records a `keep` row so `current_baseline` has a
bar. The cycles refuse a slice with no bar; this is what removes that refusal.

An incomplete run records a CRASH, never a zero. A zero is a claim about the
product; an absent measurement is a claim about the run, and confusing the two
is what made five tone numbers void.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import autoresearch_cycle  # noqa: E402
import autoresearch_reliability_cycle as reliability  # noqa: E402
import autoresearch_status  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--slice", default="shapes")
    parser.add_argument(
        "--require-idle",
        type=float,
        default=reliability._DEFAULT_REQUIRE_IDLE,
        help="only measure when nobody has touched the machine for this "
        "many seconds.",
    )
    parser.add_argument(
        "--describe",
        default="baseline",
        help="what this baseline records, for the results.tsv description",
    )
    arguments = parser.parse_args(argv)

    commit = autoresearch_cycle._git("rev-parse", "--short", "HEAD")
    tag = autoresearch_cycle.slice_tag(arguments.slice, kind="reliability")
    documents = len(reliability._documents())

    with autoresearch_status.showing(
        phase=f"reliability {arguments.slice} baseline",
        description=arguments.describe,
        essays=f"1-{documents}",
        experiment=commit,
        baseline="none",
    ):
        rate, reason = reliability.measure(
            ROOT / f"reliability-run-{arguments.slice}.log",
            require_idle=arguments.require_idle,
            corpus=reliability.corpus_for(arguments.slice),
        )

    if rate is None:
        autoresearch_cycle.record(
            commit, None, 0.0, "crash", f"{tag} {arguments.describe}"
        )
        print(f"CRASH {reason}")  # noqa: T201
        return 1

    autoresearch_cycle.record(
        commit, rate, 0.0, "keep", f"{tag} {arguments.describe}"
    )
    print(f"BASELINE {rate:.1f}")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
