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
import time

# Long enough for the harness's own keystrokes to age past the gate.
_SKIP_WAIT_SECONDS = 60.0
_SKIP_RETRIES = 30
_SKIPPED_NOBODY_HOME = 2

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
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="measure the unchanged tree this many times and report the "
        "spread. program.md asks for three runs before a metric's first "
        "experiment, and this slice never got them.",
    )
    arguments = parser.parse_args(argv)

    # THE SPREAD IS THE POINT, and it has to be measured before the bar is set.
    #
    # `current_baseline` takes the MAX of kept rows - a ratchet, deliberately -
    # so three runs recorded blind would quietly set the bar to the luckiest of
    # them. That is not hypothetical: on 2026-09-05 an unchanged tree baselined
    # at 66.7 and then 33.3, the bar held at 66.7, and 36 experiments in a row
    # discarded at 33.3 against it. Every one of those discards was a coin flip
    # recorded as a refuted idea.
    #
    # So each run is recorded honestly and numbered, and the spread is printed
    # at the end for a person to act on. Choosing the bar stays a decision.
    scores: list[float] = []
    for attempt in range(1, max(1, arguments.repeat) + 1):
        label = (
            arguments.describe
            if arguments.repeat == 1
            else f"{arguments.describe}, spread run {attempt} of {arguments.repeat}"
        )
        # A SKIP RETRIES THIS RUN, it does not abandon the sequence.
        #
        # THE HARNESS ITSELF TYPES, so the moment a run finishes the idle timer
        # reads zero and the next run is refused. Returning 2 here made the
        # caller restart from run 1, which then measured, reset the timer, and
        # was refused again - a loop that could record run one indefinitely and
        # never reach run two. Waiting is the whole answer, and the wait is what
        # the gate is for.
        for _wait in range(_SKIP_RETRIES):
            code = _measure_once(arguments, label, scores)
            if code != _SKIPPED_NOBODY_HOME:
                break
            print(  # noqa: T201
                f"  waiting for the machine to go quiet before spread run "
                f"{attempt}",
                flush=True,
            )
            time.sleep(_SKIP_WAIT_SECONDS)
        if code:
            return code
    if len(scores) > 1:
        low, high = min(scores), max(scores)
        print(  # noqa: T201
            f"SPREAD {high - low:.1f} points across {len(scores)} runs "
            f"(low {low:.1f}, high {high:.1f}, "
            f"scores {' '.join(f'{s:.1f}' for s in scores)})",
            flush=True,
        )
    return 0


def _measure_once(arguments, describe: str, scores: list[float]) -> int:

    commit = autoresearch_cycle._git("rev-parse", "--short", "HEAD")
    tag = autoresearch_cycle.slice_tag(arguments.slice, kind="reliability")
    # The SLICE's corpus, not the default one. The panel said 'essays 1-20'
    # while measuring a three-document slice, which is a third wrong claim
    # on a display whose whole job is to say what is happening.
    documents = len(reliability._documents(reliability.corpus_for(arguments.slice)))

    with autoresearch_status.showing(
        phase=f"reliability {arguments.slice} baseline",
        description=describe,
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
        # A SKIP IS NOT A CRASH. program.md: "do NOT write it to
        # results.tsv, because nothing was measured and a skipped tick is
        # not a crash." Without this an overnight loop waiting for the
        # keyboard to go quiet would fill the ledger with crashes that
        # describe the room, and a later reader would see a product
        # falling over all night.
        if "someone is at the keyboard" in reason:
            print(f"SKIPPED {reason}", flush=True)  # noqa: T201
            return 2
        autoresearch_cycle.record(
            commit, None, 0.0, "crash", f"{tag} {describe}"
        )
        print(f"CRASH {reason}")  # noqa: T201
        return 1

    autoresearch_cycle.record(commit, rate, 0.0, "keep", f"{tag} {describe}")
    scores.append(rate)
    print(f"BASELINE {rate:.1f}")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
