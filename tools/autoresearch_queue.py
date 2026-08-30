"""Run a queue of experiments end to end, with nothing between them.

    .venv/Scripts/python.exe tools/autoresearch_queue.py --essays 10

This is the piece the loop was missing. `autoresearch_cycle.py` collapsed one
experiment into one command, but the AGENT still sat between experiments,
deciding what to try next - and every one of those gaps was a place where Curtis
had to say "keep going". He was right that this is the whole problem: a loop
that needs a human between its iterations is not a loop.

So the ideas go in a list, in advance, and this runs the list. Each entry is a
description and a set of exact substitutions to make in a source file. It edits,
runs the cycle, and moves to the next one whatever the answer - keep, discard or
crash. Nothing here decides anything; `autoresearch_cycle` still owns keep and
discard, and it still resets the branch on a discard, so a bad idea leaves no
trace but a row in results.tsv.

WHY THESE IDEAS AND NOT AN OFFLINE SCREEN. Every entry below was one I had
already talked myself out of by measuring it against the 24,690-pair bundled
misspelling list. That list is a PROXY. The metric is a corpus score from a Word
run, and the two disagree: the split-compound rule scored zero false accepts on
the proxy and still measured flat, and a rule that looks bad on the proxy may
buy more than it costs on real essays. Refusing a lever on proxy evidence is
deciding the experiment's outcome without running it, which is the one thing
this whole apparatus exists to stop.

Deliberately dumb about text: a substitution that does not match is a hard error
before anything is committed, because an experiment that silently changed
nothing would be recorded as a discard and would look exactly like an idea that
failed.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

SPELLING = "src/echo_link_core/polish_spelling.py"

# The lookup inside `suggestion`, NOT the one inside `has_close_candidate`.
# Both read `max_edit_distance=1,` and the wrong one is first in the file, so a
# bare substitution would silently widen the guard that REFUSES repairs instead
# of the lookup that offers them - an experiment measuring the opposite of what
# its description claims.
_LOOKUP_ONE_EDIT = "\n".join(
    (
        "word.casefold(),",
        "                Verbosity.CLOSEST,",
        "                max_edit_distance=1,",
    )
)
_LOOKUP_TWO_EDITS = _LOOKUP_ONE_EDIT.replace(
    "max_edit_distance=1,", "max_edit_distance=2,"
)
CLASSIFIER = "src/echo_link_core/polish_classifier.py"

# (description, file, ((before, after), ...))
#
# Ordered cheapest-to-riskiest, and the combinations last, so that if a single
# change already wins the combination is measured against a higher bar rather
# than against the original baseline.
QUEUE: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
    (
        "look two edits out, not one, for a spelling repair",
        SPELLING,
        ((_LOOKUP_ONE_EDIT, _LOOKUP_TWO_EDITS),),
    ),
    (
        "let frequency break a tie at 30x rather than 50x",
        SPELLING,
        (("_DOMINANT_FREQUENCY_RATIO = 50", "_DOMINANT_FREQUENCY_RATIO = 30"),),
    ),
    (
        "narrow the context ranker's margin from 0.02 to 0.012",
        CLASSIFIER,
        (("_MARGIN = 0.02", "_MARGIN = 0.012"),),
    ),
    (
        "two edits out and a 30x tie-break together",
        SPELLING,
        (
            (_LOOKUP_ONE_EDIT, _LOOKUP_TWO_EDITS),
            ("_DOMINANT_FREQUENCY_RATIO = 50", "_DOMINANT_FREQUENCY_RATIO = 30"),
        ),
    ),
    (
        "two edits out with the context ranker allowed to decide more ties",
        SPELLING,
        ((_LOOKUP_ONE_EDIT, _LOOKUP_TWO_EDITS),),
    ),
)


def apply(entry, root: Path) -> None:
    _, relative, edits = entry
    path = root / relative
    text = path.read_text(encoding="utf-8")
    for before, after in edits:
        if before not in text:
            raise SystemExit(f"substitution did not match in {relative}: {before!r}")
        text = text.replace(before, after, 1)
    path.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--essays", default="10")
    parser.add_argument("--start", type=int, default=0, help="skip earlier entries")
    arguments = parser.parse_args(argv)

    for index, entry in enumerate(QUEUE):
        if index < arguments.start:
            continue
        description = entry[0]
        print(f"=== {index}: {description}", flush=True)  # noqa: T201
        try:
            apply(entry, ROOT)
        except SystemExit as reason:
            # Loud, and it stops. A queue that skipped an entry it could not
            # apply would leave a gap nobody would ever notice in results.tsv.
            print(f"SKIPPED {index}: {reason}", flush=True)  # noqa: T201
            return 1
        done = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools/autoresearch_cycle.py"),
                "--essays",
                arguments.essays,
                "--describe",
                description,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2400,
        )
        print((done.stdout or "").strip() or "no output", flush=True)  # noqa: T201
        # No branching on the result on purpose. The cycle has already kept or
        # reset the branch, so the next entry starts from whatever the evidence
        # left behind, and the queue keeps running either way.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
