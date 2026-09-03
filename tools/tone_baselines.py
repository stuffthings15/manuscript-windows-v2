"""Measure each tone unchanged and record it, so experiments have a bar.

`autoresearch_queue` refuses to run an experiment on a slice with no baseline
row, because a slice with no row has a bar of zero and keeps whatever it scores.
That has already happened twice on essay slices, and both only escaped being
wrong because they happened to score 100.

The tone numbers in build/acceptance/tone-*.json cannot serve as baselines: they
predate today's HUD polling fix, so they were taken from runs that ended early
and a later run is not comparable to them. Measuring fresh is the only honest
option.

    .venv/Scripts/python.exe tools/tone_baselines.py --tones Shorten Rephrase Friendly
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import autoresearch_cycle  # noqa: E402
import autoresearch_status  # noqa: E402
import autoresearch_tone_cycle as tone_cycle  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tones", nargs="+", required=True)
    arguments = parser.parse_args(argv)

    commit = autoresearch_cycle._git("rev-parse", "--short", "HEAD")
    for tone in arguments.tones:
        print(f"=== baseline {tone}", flush=True)  # noqa: T201
        # The panel shows for the whole measurement. Only the cycles used
        # to publish it, so a baseline run showed nothing and looked
        # exactly like no run at all.
        with autoresearch_status.showing(
            phase=f"tone {tone} baseline",
            description="baseline",
            essays=f"{tone_cycle.FIRST_ESSAY}-{tone_cycle.LAST_ESSAY}",
            experiment=commit,
            baseline="none",
        ):
            percent, reason = tone_cycle.measure(
                tone, ROOT / f"tone-baseline-{tone.lower()}.log"
            )
        tag = autoresearch_cycle.slice_tag(tone, kind="tone")
        if percent is None:
            # A crash row, not a zero. An absent measurement is a claim about
            # the run, never about the product - the mistake that invalidated
            # five tone numbers on 2026-08-20.
            autoresearch_cycle.record(commit, None, 0.0, "crash", f"{tag} baseline")
            print(f"CRASH {reason}", flush=True)  # noqa: T201
            continue
        autoresearch_cycle.record(commit, percent, 0.0, "keep", f"{tag} baseline")
        print(f"BASELINE {percent:.1f}", flush=True)  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
