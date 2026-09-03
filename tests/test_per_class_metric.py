"""An experiment that trades one error class for another must be visible.

`grade_corpus` has always returned five scaled terms - spelling, homophones,
punctuation, grammar, coherence - and `autoresearch_run` threw four of them away,
averaging `row["total"]` alone. So an experiment that fixed punctuation while
breaking grammar measured FLAT and was recorded as a bad idea, and every
correction experiment ever run was judged through that lens.

It also costs the loop its remaining headroom. Nine of twelve measured essay
slices sit at exactly 100 on the total and cannot improve under the
strict-improvement rule, while a slice at 100 on the total may still have real
room on grammar.

Curtis approved touching `autoresearch_run.py` for this on 2026-09-01;
`program.md` otherwise calls it ground truth.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import autoresearch_cycle  # noqa: E402

CLASSES = ("spelling", "homophones", "punctuation", "grammar", "coherence")


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "results.tsv"
    path.write_text(
        "commit\tscore\tmemory_gb\tstatus\tdescription\n", encoding="utf-8"
    )
    monkeypatch.setattr(autoresearch_cycle, "RESULTS", path)
    return path


def _row(commit, score, status, description):
    return f"{commit}\t{score:.6f}\t0.0\t{status}\t{description}\n"


def test_the_run_prints_every_class():
    """The summary must carry all five terms, not only the sum."""
    source = (ROOT / "tools" / "autoresearch_run.py").read_text(encoding="utf-8")
    for name in CLASSES:
        assert f'"{name}"' in source or f"'{name}'" in source


def test_the_total_line_is_unchanged():
    """Every row already in results.tsv must stay comparable.

    The split is additive. If `score:` changed meaning, every historic row would
    silently become incomparable with every new one.
    """
    source = (ROOT / "tools" / "autoresearch_run.py").read_text(encoding="utf-8")
    assert 'print(f"score:            {score:.6f}")' in source
    assert 'score = sum(row["total"] for row in graded) / len(graded)' in source


def test_a_class_is_parsed_from_its_own_line():
    """The cycle reads class_grammar, not score, when asked for grammar."""
    sample = "\n".join(
        [
            "score:            100.000000",
            "essays:           1",
            "class_spelling:    15.000000",
            "class_grammar:     11.500000",
            "class_punctuation: 20.000000",
        ]
    )
    found = re.search(r"^class_grammar: +([0-9.]+)", sample, re.MULTILINE)
    assert found is not None
    assert float(found.group(1)) == 11.5
    # And the total is still its own line, unaffected.
    total = re.search(r"^score: +([0-9.]+)", sample, re.MULTILINE)
    assert float(total.group(1)) == 100.0


def test_a_class_gets_its_own_slice_and_bar(ledger):
    """A grammar score of 11.5 must never be judged against a total of 100.

    Tagging both the same way would discard every grammar experiment forever,
    which is the failure this whole change exists to prevent.
    """
    ledger.write_text(
        ledger.read_text(encoding="utf-8")
        + _row("aaa1111", 100.0, "keep", "essays 10: baseline")
        + _row("aaa1111", 11.5, "keep", "essays-grammar 10: baseline"),
        encoding="utf-8",
    )

    assert autoresearch_cycle.current_baseline("10", "essays") == 100.0
    assert autoresearch_cycle.current_baseline("10", "essays-grammar") == 11.5

    # A grammar experiment scoring 12.0 is an improvement on its own bar, and
    # would have been a discard against the total.
    assert autoresearch_cycle.decide(11.5, 12.0) == "keep"
    assert autoresearch_cycle.decide(100.0, 12.0) == "discard"


def test_a_saturated_total_can_still_have_class_headroom(ledger):
    """The point of the split, stated as a test.

    Essay 10 sits at 100 on the total and cannot improve. Grammar is 20 weighted
    points before scaling, so a slice at 100 overall can still be losing marks
    inside a class.
    """
    ledger.write_text(
        ledger.read_text(encoding="utf-8")
        + _row("bbb2222", 100.0, "keep", "essays 10: baseline"),
        encoding="utf-8",
    )
    # No experiment on the total can ever be kept.
    assert autoresearch_cycle.decide(
        autoresearch_cycle.current_baseline("10", "essays"), 100.0
    ) == "discard"
    # The class slice is fresh, so it is refused until measured rather than
    # kept against a bar of zero.
    assert "no baseline" in autoresearch_cycle.missing_baseline("10", "essays-grammar")


def test_every_class_has_a_distinct_slice_tag():
    tags = {autoresearch_cycle.slice_tag("10", f"essays-{name}") for name in CLASSES}
    assert len(tags) == len(CLASSES)
    assert autoresearch_cycle.slice_tag("10", "essays") not in tags


def test_the_cycle_offers_exactly_the_scored_classes():
    """A typo in --metric must be refused, not silently measured as nothing."""
    source = (ROOT / "tools" / "autoresearch_cycle.py").read_text(encoding="utf-8")
    for name in CLASSES:
        assert f'"{name}"' in source
    assert 'choices=("total", "spelling"' in source
