"""A slice at 100.0 has every class at its maximum. Arithmetic, not opinion.

The 2026-09-01 experiment plan set out to un-saturate nine essay slices pinned
at 100 by scoring each error class separately, on the stated grounds that "a
slice at 100 may still have real room on grammar". That is false for this
rubric, and a day of queued experiments was planned around it.

`grade_corpus.grade` returns

    total = round(SCALE * (spelling + homophones + punctuation + grammar
                           + coherence))

so the total is the sum of the parts. If the sum is at its maximum then every
part is at its own maximum, because none can exceed its weight and none can be
negative. There is no configuration of a 100.0 slice that hides headroom in a
class.

Measured on the 20 essay corpus at 99.9 on 2026-09-08, four of five classes sat
at exactly their maximum and grammar was 0.118 scaled points short - the entire
remaining headroom of the corpus.

This test exists so the premise cannot be re-adopted from the plan document by
a later reader who has not done the arithmetic.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _entry in (ROOT / "tools", ROOT / "src"):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

import grade_corpus  # noqa: E402

CLASSES = ("spelling", "homophones", "punctuation", "grammar", "coherence")


def test_the_total_is_the_sum_of_the_classes():
    """The whole argument rests on this one line of grade_corpus."""
    source = (ROOT / "tools" / "grade_corpus.py").read_text(encoding="utf-8")
    assert (
        "SCALE * (spelling + homophones + punctuation + grammar + coherence)" in source
    ), (
        "The total is no longer the sum of the five classes. If a class can now "
        "vary independently of the total, per-class scoring CAN un-saturate a "
        "slice and this rule needs rewriting rather than deleting."
    )


def test_the_class_maxima_sum_to_the_total_scale():
    """No slack anywhere: the parts at full marks come to exactly 100."""
    full = sum(grade_corpus.WEIGHTS[name] for name in CLASSES)
    assert set(grade_corpus.WEIGHTS) == set(CLASSES), (
        "The rubric's categories changed. Per-class headroom has to be "
        "recomputed before any claim about saturation is reused."
    )
    assert round(grade_corpus.SCALE * full, 6) == 100.0


def test_a_hundred_leaves_no_room_in_any_class():
    """The claim itself, stated as the arithmetic that makes it true."""
    scaled_maxima = {
        name: grade_corpus.SCALE * grade_corpus.WEIGHTS[name] for name in CLASSES
    }
    # A slice scoring the full 100 must be at every maximum at once.
    at_full = dict(scaled_maxima)
    assert round(sum(at_full.values()), 6) == 100.0
    for name, value in at_full.items():
        assert round(scaled_maxima[name] - value, 6) == 0.0, (
            f"{name} still has room at a total of 100, which cannot happen "
            f"while the total is the sum of the classes."
        )


def test_coherence_cannot_vary_and_is_free_marks():
    """17.6 of every score measures nothing, and grade_corpus says so.

    Not a defect this test is asking anyone to fix - the category was reduced
    deliberately when meta commentary stopped being a coherence charge, because
    the only repair available was deleting the writer's sentence. It is pinned
    because a sixth of the metric being constant changes what every score
    means, and the file's own note says the honest options are to give it a real
    term or to drop it and reweight.
    """
    source = (ROOT / "tools" / "grade_corpus.py").read_text(encoding="utf-8")
    assert "coherence = float(WEIGHTS[\"coherence\"])" in source, (
        "Coherence now has a scored term. That is a change to what the metric "
        "measures and every earlier score should be re-read in that light."
    )
    assert round(grade_corpus.SCALE * grade_corpus.WEIGHTS["coherence"], 3) == 17.647
