"""The grader must not charge correct writing.

A metric that penalises good prose does more than misreport. It teaches an
optimiser - human or AutoResearch loop - to damage the writing in order to
score, which is the one failure the release gates exist to prevent.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from grade_corpus import _run_on_sentences  # noqa: E402


def test_one_conjunction_joining_two_nouns_is_not_a_run_on():
    """The sentence that cost essay 8 of the gated corpus 2.35 points.

    It is grammatical and correctly punctuated. The "and" joins two nouns -
    person and application - not two clauses, and the rule could not tell the
    difference. `split_run_on_sentences` correctly declines to touch it, so the
    only way to win the point was to insert a comma the writer did not need.
    """
    sentence = (
        "Another dimension appears when least-privilege access limits the "
        "damage caused by a compromised account by giving each person and "
        "application only the permissions required for work."
    )

    assert _run_on_sentences([sentence]) == []


def test_chained_conjunctions_are_still_charged():
    """Narrowing the rule must not switch it off.

    A corrector should be able to split a genuine run-on, so this charge
    measures a real product gap rather than the corpus, and it stays.
    """
    sentence = (
        "He opened the door and the cat ran out and the dog followed it into "
        "the garden and nobody could catch them before dark and it rained again"
    )

    assert _run_on_sentences([sentence]) == [sentence]


def test_a_sentence_with_punctuation_is_never_a_run_on():
    """Commas are what a run-on is missing; their presence settles it."""
    sentence = (
        "He opened the door, and the cat ran out, and the dog followed it into "
        "the garden, and nobody could catch them before dark and it rained"
    )

    assert _run_on_sentences([sentence]) == []
