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


def test_only_one_paragraph_count_is_not_a_reshaped_run():
    """One number and not the other is a missing measurement, not damage.

    outcome() and _reshaped_runs() have to agree about this or the summary and
    the gate say different things about the same row. outcome() compared with
    .get() on both sides, so a row holding only paragraphs_before was
    "reshaped" (None != 15) while _reshaped_runs, which requires both, called
    it clean.
    """
    row = _row()
    del row["paragraphs_after"]

    assert run_essays_batch.outcome(row) == "complete"


def test_the_tiers_fail_on_a_reshaped_run():
    """The list existing is not the gate working.

    The nine tests above cover outcome() and _reshaped_runs() and stopped
    there. Deleting the whole `if reshaped` block from a tier left all of them
    green, which is the failure mode CLAUDE.md names - a test that asserts the
    shape of a call rather than its effect - and it is why two of the three
    tier paths were written without a check at all.
    """
    damaged = [{
        "essay": "16_sustainable_fashion_and_textile_waste",
        "paragraphs_before": 15,
        "paragraphs_after": 14,
    }]

    line = release_acceptance._reshaped_failure("corrections smoke", damaged)

    assert line
    assert "16_sustainable_fashion_and_textile_waste" in line
    assert "15 -> 14" in line
    assert release_acceptance._reshaped_failure("anything", []) == ""


def test_every_word_batch_in_the_gate_asks_about_reshaping():
    """All three places that run essays through Word, not just one.

    The smoke, the twenty-essay corpora and the tone runs each drive Word and
    each can produce a reshaped document. The first version of this gate asked
    in one of the three, and the tone runs - the only place the defect has ever
    actually been observed - were not the one.
    """
    source = Path(release_acceptance.__file__).read_text(encoding="utf-8")
    batch_runs = source.count('"tools/run_essays_batch.py",')
    asked = source.count("_reshaped_failure(")

    assert batch_runs == 3, f"the gate now drives Word {batch_runs} times"
    # Three call sites plus the definition.
    assert asked >= batch_runs, (
        f"{batch_runs} Word batches but only {asked} reshaped checks; a batch "
        f"that is never asked about can damage a document and still pass"
    )


def test_a_table_is_not_mistaken_for_a_reshaped_document():
    """The two ends of a run do not count paragraphs the same way.

    `before` is built from the .docx XML, one entry per w:p. `after` is Word's
    COM Content.Text, where every table cell and row terminator is CR+BEL.
    U+0007 is not whitespace, so `.strip()` kept it and each row end became a
    paragraph of its own.

    Measured on a heading plus a 2x2 table with nothing changed at all: 5
    paragraphs in, 8 out. Once a moved paragraph count fails a run, that is not
    cosmetic - every document containing a table would be reported damaged on a
    perfect run, and a gate that cries wolf is a gate people learn to skip.
    06_table.docx is in the live autoresearch `open` slice.
    """
    # What Word COM hands back for a heading and a 2x2 table.
    com_text = "Title\rAlpha\r\x07Beta\r\x07\r\x07Gamma\r\x07Delta\r\x07\r\x07"
    # What docx_paragraphs gives for the same document.
    xml_text = "Title\nAlpha\nBeta\nGamma\nDelta"

    before = run_essays_batch.paragraphs(xml_text)
    after = run_essays_batch.paragraphs(com_text)

    assert before == ["Title", "Alpha", "Beta", "Gamma", "Delta"]
    assert after == before
    assert run_essays_batch.outcome(
        _row(paragraphs_before=len(before), paragraphs_after=len(after))
    ) == "complete"


def test_a_real_table_document_still_reports_its_own_paragraphs():
    """Stripping the cell mark must not also swallow the cell's text."""
    com_text = "Heading\rleft cell\r\x07right cell\r\x07\r\x07"

    assert run_essays_batch.paragraphs(com_text) == [
        "Heading",
        "left cell",
        "right cell",
    ]


def test_the_research_loop_names_a_reshaped_row_rather_than_calling_it_unknown():
    """The loop exists to tell a harness failure from a product failure.

    _row_status matched the outcome vocabulary as literal substrings and was
    not extended, so a reshaped row fell through every branch to "unknown" and
    the loop printed `crash: unknown` for the one failure whose cause is
    already known by name.
    """
    import autoresearch_run

    row = " 16 16_sustainable_fashion      run1  226.3s reshaped  presses=21"

    assert autoresearch_run._row_status(row) == "reshaped"
    # And the words it already knew still read the same way.
    assert autoresearch_run._row_status(row.replace("reshaped", "complete")) == "complete"
    assert autoresearch_run._row_status(row.replace("reshaped", "partial")) == "partial"
    assert autoresearch_run._row_status(row.replace("reshaped", "dead")) == "dead"
