"""Plot the night's experiments from results.tsv.

The Manuscript equivalent of `analysis.ipynb` in
https://github.com/karpathy/autoresearch. It is part of the deliverable rather
than a nicety: the whole design promises that you wake up to a log of
experiments, and a 200-row TSV is not a log anyone reads.

    .venv/Scripts/python.exe tools/autoresearch_analysis.py
    .venv/Scripts/python.exe tools/autoresearch_analysis.py --out progress.png

Discarded runs are faint grey, kept runs are prominent green, crashes sit on the
baseline in red, and a step line tracks the running best. Every kept run is
labelled with its description, because "which change actually moved it" is the
only question this plot exists to answer.

It also prints the spread of the repeated baseline rows, which is the number
Phase 2 of the plan exists to get. Upstream can trust a single five-minute run
because val_bpb on a fixed validation set is stable; a single Manuscript essay
may not be, and until the spread is known, every keep and discard is partly a
coin toss. Any run whose description begins "baseline" is treated as a repeat of
the same unchanged configuration.

Reads results.tsv and writes an image. It changes nothing else.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

KEPT = "keep"
DISCARDED = "discard"
CRASHED = "crash"


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        print(f"no {path} yet - nothing has been run", file=sys.stderr)  # noqa: T201
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        # Tab separated on purpose. Descriptions contain commas, and a comma
        # separated file silently splits "increase LR, then widen" into two
        # columns and shifts every field after it.
        return list(csv.DictReader(handle, delimiter="\t"))


def baseline_spread(rows: list[dict]) -> tuple[float, float, float, int] | None:
    """min, max, spread and count over the repeated unchanged baseline."""
    # Grouped BY SLICE, and the slice prefix stripped before the "baseline"
    # test. Descriptions carry "essays 10: " since the baseline became per-slice,
    # so a plain startswith("baseline") matched nothing and this quietly reported
    # "NO BASELINE SPREAD YET" forever - the failure mode of a check that can
    # only ever say the cautious thing.
    #
    # Repeats of one slice are the only comparable set. Pooling essay 8's 98 with
    # essay 10's 94 would report a spread of 4 points for a measurement whose
    # real spread is zero, and every honest keep would then look like noise.
    by_slice: dict[str, list[float]] = {}
    for row in rows:
        if row.get("status") == CRASHED or not _is_number(row.get("score")):
            continue
        description = str(row.get("description", "")).strip()
        slice_name, _, rest = description.partition(":")
        if not rest or not slice_name.casefold().startswith("essays"):
            slice_name, rest = "", description
        if not rest.strip().casefold().startswith("baseline"):
            continue
        by_slice.setdefault(slice_name, []).append(float(row["score"]))
    repeated = [scores for scores in by_slice.values() if len(scores) >= 2]
    if not repeated:
        return None
    # The WIDEST spread any one slice showed, because the question this answers
    # is how far a change must move before it means anything anywhere.
    widest = max(repeated, key=lambda scores: max(scores) - min(scores))
    return (
        min(widest),
        max(widest),
        max(widest) - min(widest),
        sum(len(scores) for scores in repeated),
    )


def _is_number(value) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results", type=Path, default=ROOT / "results.tsv")
    parser.add_argument("--out", type=Path, default=ROOT / "progress.png")
    arguments = parser.parse_args(argv)

    rows = read_rows(arguments.results)
    if not rows:
        return 1

    scored = [row for row in rows if _is_number(row.get("score"))]
    kept = [row for row in scored if row.get("status") == KEPT]
    print(f"{len(rows)} experiments, {len(kept)} kept")  # noqa: T201

    spread = baseline_spread(rows)
    if spread is None:
        print(  # noqa: T201
            "\nNO BASELINE SPREAD YET. Run the unchanged baseline at least "
            "twice before trusting any keep or discard: without it there is no "
            "way to tell a real gain from a lucky run."
        )
    else:
        low, high, width, count = spread
        print(  # noqa: T201
            f"\nbaseline spread over {count} unchanged runs: "
            f"{low:.2f} to {high:.2f}, width {width:.2f}"
        )
        print(  # noqa: T201
            f"  a change should clear about {width:.2f} points before it means "
            f"anything"
        )

    print("\nkept experiments, best last:")  # noqa: T201
    for row in sorted(kept, key=lambda r: float(r["score"])):
        print(  # noqa: T201
            f"  {row['commit']:<9}{float(row['score']):>9.3f}  {row['description']}"
        )

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("\nmatplotlib is not installed; printed summary only", file=sys.stderr)  # noqa: T201
        return 0

    figure, axis = plt.subplots(figsize=(16, 8))
    best = None
    best_x: list[int] = []
    best_y: list[float] = []
    for index, row in enumerate(rows):
        if not _is_number(row.get("score")):
            continue
        score = float(row["score"])
        status = row.get("status")
        if status == CRASHED or score == 0:
            axis.plot(index, 0, "x", color="#e74c3c", markersize=7)
            continue
        if status == KEPT:
            axis.plot(index, score, "o", color="#2ecc71", markersize=9)
            axis.annotate(
                row.get("description", "")[:38],
                (index, score),
                textcoords="offset points",
                xytext=(0, 9),
                fontsize=7,
                color="#1a7a3a",
                rotation=20,
            )
        else:
            axis.plot(index, score, "o", color="#cccccc", markersize=5)
        # Higher is better here, unlike upstream where lower bpb wins.
        best = score if best is None else max(best, score)
        best_x.append(index)
        best_y.append(best)
    if best_x:
        axis.step(best_x, best_y, where="post", color="#27ae60", linewidth=1.6)

    axis.set_xlabel("experiment")
    axis.set_ylabel("corpus score (higher is better)")
    axis.set_title("Manuscript AutoResearch progress")
    axis.grid(alpha=0.25)
    figure.tight_layout()
    figure.savefig(arguments.out, dpi=110)
    print(f"\nwrote {arguments.out}")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
