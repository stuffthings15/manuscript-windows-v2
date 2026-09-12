"""A review that changes the document's shape must fail, at any score.

`run_essays_batch.score` has counted paragraphs at both ends since the harness
was written. Nothing ever compared the two numbers.

Measured 2026-09-11, essay 16 of the error-riddled corpus, through Word as a
writer with the Clear tone: in went 15 paragraphs, out came 14. The summary
recorded `paragraphs_before: 15, paragraphs_after: 14` and `outcome: complete`
on the same row. The acceptance tier read that row and did not object. The tone
judge caught it fifteen minutes later, by accident, because its own source and
result section counts stopped agreeing - and only because essay 16 happened to
be one of the essays it judged.

Two places had the evidence and threw it away, so both are tested here:

  * `outcome()` decided in one word what happened and never looked at the two
    numbers sitting in the row it was handed.
  * `release_acceptance` gathered incomplete runs and graded the rest. A
    reshaped run is not incomplete - it pressed every section and its score is
    honest - so it passed straight through the gate.

The second is kept separate from `_incomplete_runs` deliberately. "Did the run
finish" and "did the document survive it" are different questions with
different answers and different fixes, and the docstring on
`_run_was_truncated` records what conflating two such questions already cost.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import release_acceptance  # noqa: E402
import run_essays_batch  # noqa: E402


def _row(**overrides) -> dict:
    """A row from a run that went perfectly, before anything is broken in it."""
    row = {
        "essay": "16_sustainable_fashion_and_textile_waste",
        "error": None,
        "presses": 21,
        "unchanged": [],
        "stalled_at": None,
        "paragraphs_before": 15,
        "paragraphs_after": 15,
        "stopped_because": "the review finished after press 21; section 21/21",
    }
    row.update(overrides)
    return row


def test_a_clean_run_is_still_complete():
    """The guard must not turn ordinary good runs into failures."""
    assert run_essays_batch.outcome(_row()) == "complete"


def test_a_row_with_no_paragraph_counts_is_judged_on_what_it_has():
    """A row that never reached `score` has neither number.

    A missing measurement is not a failed one, and inventing a failure from an
    absent field would make every early error look like structural damage.
    """
    row = _row()
    del row["paragraphs_before"]
    del row["paragraphs_after"]

    assert run_essays_batch.outcome(row) == "complete"


def test_a_lost_paragraph_is_not_a_complete_run():
    """15 in, 14 out. The exact row the acceptance tier called complete."""
    assert run_essays_batch.outcome(_row(paragraphs_after=14)) == "reshaped"


def test_an_added_paragraph_is_caught_too():
    """A review that SPLITS a paragraph is the same defect facing the other way."""
    assert run_essays_batch.outcome(_row(paragraphs_after=16)) == "reshaped"


def test_structural_damage_outranks_an_unchanged_paragraph():
    """Both wrong at once must report the one the writer cannot undo.

    An unchanged paragraph is a quality signal and the writer can re-run the
    essay. A deleted paragraph break cannot be clicked back.
    """
    row = _row(paragraphs_after=14, unchanged=[3])

    assert run_essays_batch.outcome(row) == "reshaped"


def _summary(tmp_path, monkeypatch, rows):
    path = tmp_path / "batch-summary.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    monkeypatch.setattr(release_acceptance, "SUMMARY", path)
    return path


def test_the_gate_reports_the_reshaped_essay_by_name(tmp_path, monkeypatch):
    _summary(
        tmp_path,
        monkeypatch,
        [
            _row(essay="01_urban_beekeeping", outcome="complete"),
            _row(paragraphs_after=14, outcome="reshaped"),
        ],
    )

    reshaped = release_acceptance._reshaped_runs()

    assert len(reshaped) == 1
    found = reshaped[0]
    assert found["essay"] == "16_sustainable_fashion_and_textile_waste"
    assert found["paragraphs_before"] == 15
    assert found["paragraphs_after"] == 14


def test_the_gate_stays_quiet_when_every_document_kept_its_shape(tmp_path, monkeypatch):
    _summary(
        tmp_path,
        monkeypatch,
        [_row(essay="01_urban_beekeeping"), _row(essay="02_public_libraries")],
    )

    assert release_acceptance._reshaped_runs() == []


def test_a_missing_summary_is_not_a_reshaped_run(tmp_path, monkeypatch):
    """No evidence is not evidence of damage; the gate has other rules for that."""
    monkeypatch.setattr(release_acceptance, "SUMMARY", tmp_path / "absent.json")

    assert release_acceptance._reshaped_runs() == []


def test_a_reshaped_run_is_not_counted_as_an_incomplete_one(tmp_path, monkeypatch):
    """It finished. Grading it is right; passing it is not.

    Excluding it the way an interrupted run is excluded would throw away a real
    measurement and report a mean over nineteen essays as though it were twenty.
    """
    _summary(tmp_path, monkeypatch, [_row(paragraphs_after=14, outcome="reshaped")])

    assert release_acceptance._incomplete_runs() == []
    assert release_acceptance._reshaped_runs()
