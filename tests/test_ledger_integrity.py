"""The ledger must not be able to record a bar that nothing measured.

Two holes, both found on 2026-09-01 by the first reliability experiment.

The first: `current_baseline` takes the MAX of kept scores on a slice, which is
right - the bar is the best result kept so far, a ratchet, and taking the most
recent instead would let a regression lower it. It is also why one wrong `keep`
poisons a slice permanently. The reliability baseline recorded 100.0 on a batch
where a document had visibly failed; the corrected 80.0 row was appended below
and the bar stayed at 100.0, so the next experiment was judged against a number
nothing supported. `decide` writes only keep, discard and crash, so retiring the
row meant hand-editing results.tsv.

The second: the queue has refused a slice with no baseline since essays 4 and 6
were judged against 0.00 and escaped being wrong only because both scored 100.
The CYCLES never had that guard, so calling one directly on a fresh slice kept
whatever it scored.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import autoresearch_cycle  # noqa: E402


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """A results.tsv of our own, so a test never touches the real record."""
    path = tmp_path / "results.tsv"
    path.write_text(
        "commit\tscore\tmemory_gb\tstatus\tdescription\n", encoding="utf-8"
    )
    monkeypatch.setattr(autoresearch_cycle, "RESULTS", path)
    return path


def _row(commit, score, status, description):
    return f"{commit}\t{score:.6f}\t0.0\t{status}\t{description}\n"


def test_a_kept_row_sets_the_bar(ledger):
    ledger.write_text(
        ledger.read_text(encoding="utf-8")
        + _row("aaa1111", 80.0, "keep", "reliability shapes: baseline"),
        encoding="utf-8",
    )
    assert autoresearch_cycle.current_baseline("shapes", "reliability") == 80.0


def test_a_wrong_high_row_poisons_the_bar_until_it_is_voided(ledger):
    """The exact 2026-09-01 shape: a wrong 100.0 above a corrected 80.0."""
    ledger.write_text(
        ledger.read_text(encoding="utf-8")
        + _row("d6c75f1", 100.0, "keep", "reliability shapes: baseline")
        + _row("d6c75f1", 80.0, "keep", "reliability shapes: baseline, corrected"),
        encoding="utf-8",
    )
    # Appending the correction does not lower a max. This is the bug.
    assert autoresearch_cycle.current_baseline("shapes", "reliability") == 100.0

    changed = autoresearch_cycle.void_row(
        "d6c75f1",
        "shapes",
        "reliability",
        100.0,
        "measured before the gate counted a stated stop reason as truncated",
    )

    assert changed == 1
    assert autoresearch_cycle.current_baseline("shapes", "reliability") == 80.0


def test_voiding_names_the_reason_in_the_row(ledger):
    ledger.write_text(
        ledger.read_text(encoding="utf-8")
        + _row("bbb2222", 100.0, "keep", "reliability shapes: baseline"),
        encoding="utf-8",
    )

    autoresearch_cycle.void_row("bbb2222", "shapes", "reliability", 100.0, "bad metric")

    line = [
        row for row in ledger.read_text(encoding="utf-8").splitlines()
        if row.startswith("bbb2222")
    ][0]
    parts = line.split("\t")
    assert parts[3] == "void"
    assert "VOID: bad metric" in parts[4]
    # The score is never rewritten. Only a status and a description change.
    assert parts[1] == "100.000000"


def test_voiding_touches_only_the_named_row(ledger):
    ledger.write_text(
        ledger.read_text(encoding="utf-8")
        + _row("ccc3333", 100.0, "keep", "reliability shapes: baseline")
        + _row("ccc3333", 92.9, "keep", "tone Improve: baseline")
        + _row("ddd4444", 100.0, "keep", "reliability shapes: another"),
        encoding="utf-8",
    )

    changed = autoresearch_cycle.void_row("ccc3333", "shapes", "reliability", 100.0, "x")

    assert changed == 1
    # A different slice on the same commit is untouched.
    assert autoresearch_cycle.current_baseline("Improve", "tone") == 92.9
    # A different commit on the same slice is untouched, and still sets the bar.
    assert autoresearch_cycle.current_baseline("shapes", "reliability") == 100.0


def test_a_slice_with_no_baseline_is_refused(ledger):
    assert "no baseline" in autoresearch_cycle.missing_baseline("shapes", "reliability")
    assert "no baseline" in autoresearch_cycle.missing_baseline("Friendly", "tone")


def test_a_slice_with_a_baseline_is_allowed(ledger):
    ledger.write_text(
        ledger.read_text(encoding="utf-8")
        + _row("eee5555", 80.0, "keep", "reliability shapes: baseline"),
        encoding="utf-8",
    )
    assert autoresearch_cycle.missing_baseline("shapes", "reliability") == ""


def test_a_crashed_or_voided_row_does_not_count_as_a_baseline(ledger):
    """Five of six tones sit on crash and void rows and must stay refused."""
    ledger.write_text(
        ledger.read_text(encoding="utf-8")
        + _row("fff6666", 0.0, "crash", "tone Friendly: baseline")
        + _row("fff6666", 35.7, "void", "tone Friendly: baseline"),
        encoding="utf-8",
    )
    assert autoresearch_cycle.current_baseline("Friendly", "tone") == 0.0
    assert "no baseline" in autoresearch_cycle.missing_baseline("Friendly", "tone")


def test_a_lower_is_better_metric_is_not_refused_for_a_zero_baseline(ledger):
    """Zero is not a floor to beat for latency, it is an impossible one.

    `decide` already treats a zero baseline as "no bar" in that direction, so
    refusing here would contradict it.
    """
    assert autoresearch_cycle.missing_baseline(
        "1-3", "latency", higher_is_better=False
    ) == ""


def test_both_cycles_refuse_before_they_commit_anything():
    """A refusal must not leave an experiment commit stranded with no reset.

    The guard was first written after the commit, which would have committed an
    experiment and then returned without resetting it.
    """
    for name in ("autoresearch_reliability_cycle", "autoresearch_tone_cycle"):
        module = importlib.import_module(name)
        source = Path(module.__file__).read_text(encoding="utf-8")
        guard = source.index("missing_baseline")
        commit = source.index('_git("commit"')
        assert guard < commit, f"{name} commits before it checks the baseline"


def test_voiding_refuses_when_it_cannot_name_a_single_row(ledger):
    """Two rows with the same commit, slice AND score cannot be told apart.

    Refusing is the safe direction: a bar left too high wastes experiments, a
    corrected row silently retired loses the measurement.
    """
    ledger.write_text(
        ledger.read_text(encoding="utf-8")
        + _row("ggg7777", 100.0, "keep", "reliability shapes: baseline")
        + _row("ggg7777", 100.0, "keep", "reliability shapes: baseline again"),
        encoding="utf-8",
    )

    assert autoresearch_cycle.void_row(
        "ggg7777", "shapes", "reliability", 100.0, "ambiguous"
    ) == 0
    assert autoresearch_cycle.current_baseline("shapes", "reliability") == 100.0


def test_voiding_a_row_that_is_not_there_changes_nothing(ledger):
    before = ledger.read_text(encoding="utf-8")
    assert autoresearch_cycle.void_row(
        "nosuch", "shapes", "reliability", 100.0, "x"
    ) == 0
    assert ledger.read_text(encoding="utf-8") == before
