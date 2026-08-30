"""Run a queue of experiments end to end, with nothing between them.

    .venv/Scripts/python.exe tools/autoresearch_queue.py

This is the piece the loop was missing. `autoresearch_cycle.py` collapsed one
experiment into one command, but the AGENT still sat between experiments,
deciding what to try next - and every one of those gaps was a place where Curtis
had to say "keep going". He was right that this is the whole problem: a loop
that needs a human between its iterations is not a loop.

So the ideas go in a list, in advance, and this runs the list. Each entry is a
description, a set of exact substitutions to make in a source file, and the
slice to measure them on. It edits, runs the cycle, and moves to the next one
whatever the answer - keep, discard or crash. Nothing here decides anything;
`autoresearch_cycle` still owns keep and discard, and it still resets the branch
on a discard, so a bad idea leaves no trace but a row in results.tsv.

WHY THESE IDEAS AND NOT AN OFFLINE SCREEN. The first five entries were all ones
I had talked myself out of by measuring them against the 24,690-pair bundled
misspelling list. That list is a PROXY. The metric is a corpus score from a Word
run, and the two disagree in BOTH directions: the split-compound rule scored
zero false accepts on the proxy and measured flat, while the tie-break ratio and
the ranker margin looked like bad trades on the proxy and together took essay 10
from 96 to 100. Refusing a lever on proxy evidence decides the experiment's
outcome without running it, which is the one thing this apparatus exists to stop.

Deliberately dumb about text: a substitution matching neither its before nor its
after form is a hard error, because an experiment that silently changed nothing
would be recorded as a discard and would look exactly like an idea that failed.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import autoresearch_cycle  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

SPELLING = "src/echo_link_core/polish_spelling.py"
CLASSIFIER = "src/echo_link_core/polish_classifier.py"

# The lookup inside `suggestion`, NOT the one inside `has_close_candidate`.
# Both read `max_edit_distance=1,` and the wrong one is FIRST in the file, so a
# bare substitution would silently widen the guard that REFUSES repairs instead
# of the lookup that offers them - an experiment measuring the opposite of what
# its own description claims.
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

_TAILS_BEFORE = '        "only", "more", "most",'
_TAILS_AFTER = "\n".join(
    (
        '        "only", "more", "most",',
        "        # Prepositions and pronouns. Everything above is a modal, a",
        '        # determiner or a conjunction, which is why "vendorsmay" split',
        '        # and "closelyat" did not - an arbitrary gap rather than a',
        "        # decision anyone took.",
        '        "at", "in", "on", "of", "to", "by", "as", "or", "up", "out",',
        '        "is", "be", "it", "its", "his", "her", "our", "your",',
    )
)

_PREFIXES_BEFORE = (
    '    "pre", "post", "anti", "non", "semi", "sub", "super", "inter", "ultra",'
)
_PREFIXES_AFTER = "\n".join(
    (
        _PREFIXES_BEFORE,
        "    # Native English prefixes, every bit as productive as the Greek and",
        '    # Latin ones above and left out for no stated reason. "outplanting"',
        "    # is ordinary English that both bundled lists lack, and",
        "    # RELEASE_ACCEPTANCE.md names it as a word the spelling term charges",
        "    # wrongly. DERIVING it is exactly what this table is for; listing the",
        "    # word itself would be memorising the corpus.",
        '    "out", "over", "under", "up", "down", "off", "back", "cross",',
        '    "counter", "self", "re", "un", "mis", "co",',
    )
)

_SUFFIX_CONSTANT_BEFORE = "_COMPOUND_PREFIXES = ("
_SUFFIX_CONSTANT_AFTER = "\n".join(
    (
        "# Productive English suffixes. A known stem carrying one of these is a",
        '# real word: "passivation" is passive + ation, ordinary English that',
        "# neither bundled list has. Same principle as the prefixes below -",
        "# derive the word, never list it.",
        "_COMPOUND_SUFFIXES = (",
        '    "ation", "ations", "isation", "ization", "ising", "izing",',
        '    "ised", "ized", "ment", "ments", "ness", "less", "able", "ible",',
        ")",
        "",
        "_COMPOUND_PREFIXES = (",
    )
)

_SUFFIX_LOOP_BEFORE = (
    '        # Both parts at least four characters: shorter splits are how "acid" +'
)
_SUFFIX_LOOP_AFTER = "\n".join(
    (
        "        for suffix in _COMPOUND_SUFFIXES:",
        "            if not folded.endswith(suffix):",
        "                continue",
        "            stem = folded[: -len(suffix)]",
        "            # The stem as written, and with a dropped final -e restored:",
        '            # "passivation" is passive + ation with the e elided, which',
        "            # is how English actually forms these.",
        '            for candidate in (stem, stem + "e"):',
        "                if len(candidate) > 3 and real(candidate) and common(candidate):",
        "                    return True",
        "",
        _SUFFIX_LOOP_BEFORE,
    )
)

# (description, file, ((before, after), ...), essays)
#
# The slice is PER ENTRY, because each lever below aims at the one word a
# particular essay has left. Measuring it on an essay that does not contain that
# word would score flat and read as a failed idea.
#
# Ordered cheapest to riskiest, combinations last, so that a combination is
# measured against whatever bar its own parts have already raised.
QUEUE: tuple[tuple[str, str, tuple[tuple[str, str], ...], str], ...] = (
    (
        "look two edits out, not one, for a spelling repair",
        SPELLING,
        ((_LOOKUP_ONE_EDIT, _LOOKUP_TWO_EDITS),),
        "10",
    ),
    (
        "let frequency break a tie at 30x rather than 50x",
        SPELLING,
        (("_DOMINANT_FREQUENCY_RATIO = 50", "_DOMINANT_FREQUENCY_RATIO = 30"),),
        "10",
    ),
    (
        "narrow the context ranker's margin from 0.02 to 0.012",
        CLASSIFIER,
        (("_MARGIN = 0.02", "_MARGIN = 0.012"),),
        "10",
    ),
    (
        "let a run-together word end in a preposition, not only a verb",
        SPELLING,
        ((_TAILS_BEFORE, _TAILS_AFTER),),
        "6",
    ),
    (
        "recognise out-, over- and under- as productive prefixes",
        SPELLING,
        ((_PREFIXES_BEFORE, _PREFIXES_AFTER),),
        "4",
    ),
    (
        "recognise a known stem carrying a productive suffix",
        SPELLING,
        (
            (_SUFFIX_CONSTANT_BEFORE, _SUFFIX_CONSTANT_AFTER),
            (_SUFFIX_LOOP_BEFORE, _SUFFIX_LOOP_AFTER),
        ),
        "5",
    ),
    # Two edits out, retried. It was DISCARDED on essay 10 and that verdict
    # stands for the tree it ran against - but the tie-break ratio has come down
    # to 30 since, and that changes what a wider lookup can deliver. "serreous"
    # finds serious at 38.3M against ferrous at 1.0M: a 38x lead, refused as a
    # tie under the old 50x bar and decisive under the new one. The lookup never
    # reached those candidates because it only ever looked one edit out.
    #
    # A discard is a verdict on an idea in a context, not on the idea forever.
    (
        "look two edits out, now that a 38x lead is no longer a tie",
        SPELLING,
        ((_LOOKUP_ONE_EDIT, _LOOKUP_TWO_EDITS),),
        "12",
    ),
    # "mayfund" is may + fund, and it fails on one character: the rule wants the
    # content half to be five long and "fund" is four. The comment on the
    # constant records what 4 cost when it was last measured - gallare, hearwith,
    # origonly, beththe - so this is a known trade being re-measured against the
    # metric rather than against that note.
    (
        "let the content half of a run-together word be four letters",
        SPELLING,
        (("_RUN_TOGETHER_HEAD = 5", "_RUN_TOGETHER_HEAD = 4"),),
        "13",
    ),
    # "makels" is makes at 87.8M against makers at 13.3M - a 6.6x lead, still a
    # tie at 30x. Rather than drop the ratio again and let raw frequency decide
    # more often, narrow the ranker's margin so the SENTENCE gets to decide: it
    # is the layer that can tell "makels sense" from a plural noun, and 0.02 to
    # 0.012 already took essay 10 to 100.
    (
        "narrow the ranker's margin again, from 0.012 to 0.008",
        CLASSIFIER,
        (("_MARGIN = 0.012", "_MARGIN = 0.008"),),
        "19",
    ),
)


def apply(entry, root: Path) -> bool:
    """Make this entry's change. True when something actually changed.

    A substitution whose result is ALREADY in the file is not a failure, and
    treating it as one halted the first run. Entry 1 raised the tie-break ratio
    to 30 and was KEPT, so the entry combining it with a wider lookup could no
    longer find `= 50` and the queue stopped with two experiments unrun.
    Combination entries follow their own parts by construction, so this is the
    normal case rather than an edge case.

    A substitution matching NEITHER form is still a hard error: the anchor is
    wrong, and an experiment that silently changed nothing would be recorded as
    a discard and read as an idea that failed.
    """
    _, relative, edits, _essays = entry
    path = root / relative
    text = path.read_text(encoding="utf-8")
    changed = False
    for before, after in edits:
        if before in text:
            text = text.replace(before, after, 1)
            changed = True
        elif after not in text:
            raise SystemExit(f"substitution did not match in {relative}: {before!r}")
    path.write_text(text, encoding="utf-8")
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start", type=int, default=0, help="skip earlier entries")
    parser.add_argument(
        "--essays",
        default="",
        help="override every entry's slice. Normally leave this alone: each "
        "entry names the essay holding the word it targets.",
    )
    arguments = parser.parse_args(argv)

    for index, entry in enumerate(QUEUE):
        if index < arguments.start:
            continue
        description, _relative, _edits, essays = entry
        essays = arguments.essays or essays
        print(f"=== {index} (essay {essays}): {description}", flush=True)  # noqa: T201
        if autoresearch_cycle.current_baseline(essays) <= 0.0:
            # No baseline row for this slice means the bar is zero and the next
            # result is kept whatever it scores. That has already happened twice:
            # essays 4 and 6 were judged against 0.00, and only escaped being
            # wrong because both happened to score 100.
            #
            # Refusing here rather than warning, because a keep recorded against
            # no baseline is worse than a missing experiment - it puts a number
            # in results.tsv that nothing supports, and every later comparison on
            # that slice inherits it.
            print(  # noqa: T201
                f"NO BASELINE for essay {essays}. Measure it unchanged and "
                f"record the row before running experiments on this slice.",
                flush=True,
            )
            return 2
        try:
            changed = apply(entry, ROOT)
        except SystemExit as reason:
            # Loud, and it stops. A bad anchor makes every later entry suspect,
            # and a queue that skipped one would leave a gap in results.tsv that
            # nobody would ever notice.
            print(f"SKIPPED {index}: {reason}", flush=True)  # noqa: T201
            return 1
        if not changed:
            # Everything this entry asks for is already in the tree, because an
            # earlier entry was kept. There is nothing to measure and the cycle
            # would return NOCHANGE, so say so and carry on.
            print(f"ALREADY IN {index}: {description}", flush=True)  # noqa: T201
            continue
        done = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools/autoresearch_cycle.py"),
                "--essays",
                essays,
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
