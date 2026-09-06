"""A run that stopped part way must never become a number.

Both defects below produced a wrong number on 2026-09-03, within minutes of each
other, on the first unattended loop.

ONE. The idle gate stopped the hard slice after 1 of 3 documents - correctly,
somebody was typing - and the cycle averaged that single partial document into
0.0 and recorded it as the slice's BASELINE. Every experiment afterwards would
have been judged against a number describing an interruption. This is the same
failure the codebase already carries a scar for: scoring partial runs is how 11
of 20 essays were once averaged into a mean of 84.5 and reported as a regression
that had not happened.

TWO. That 0.0 was then unusable anyway, because the queue asked
`current_baseline(...) <= 0.0` and a slice measured at zero is indistinguishable
from a slice never measured. For a completion metric zero is an ordinary result
and a perfectly good bar - any completion at all beats it - so the queue refused
to run a single experiment on a slice that had just been measured.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import autoresearch_cycle  # noqa: E402
import autoresearch_reliability_cycle as reliability  # noqa: E402

HEADER = "commit\tval\tmem\tstatus\tdescription\n"


def _ledger(tmp_path, monkeypatch, rows):
    path = tmp_path / "results.tsv"
    path.write_text(HEADER + "".join(rows), encoding="utf-8")
    monkeypatch.setattr(autoresearch_cycle, "RESULTS", path)
    return path


def test_a_slice_measured_at_zero_has_a_baseline(tmp_path, monkeypatch):
    """Zero is a result. The three hard documents all fail today, so zero is
    exactly what an honest baseline says, and every experiment can beat it."""
    tag = autoresearch_cycle.slice_tag("hard", "reliability")
    _ledger(tmp_path, monkeypatch, [f"abc1234\t0.000000\t0.0\tkeep\t{tag} baseline\n"])
    assert autoresearch_cycle.has_baseline("hard", "reliability")
    assert autoresearch_cycle.missing_baseline("hard", "reliability") == ""


def test_a_slice_never_measured_has_none(tmp_path, monkeypatch):
    """The guard that must survive: essays 4 and 6 were once judged against 0.00
    and escaped being wrong only because both happened to score 100."""
    _ledger(tmp_path, monkeypatch, [])
    assert not autoresearch_cycle.has_baseline("hard", "reliability")
    assert autoresearch_cycle.missing_baseline("hard", "reliability")


def test_a_crashed_row_is_not_a_baseline(tmp_path, monkeypatch):
    """A crash is a claim about the run. It gives no bar."""
    tag = autoresearch_cycle.slice_tag("hard", "reliability")
    _ledger(tmp_path, monkeypatch, [f"abc1234\t0.000000\t0.0\tcrash\t{tag} baseline\n"])
    assert not autoresearch_cycle.has_baseline("hard", "reliability")


def test_a_voided_row_is_not_a_baseline(tmp_path, monkeypatch):
    """Voiding exists precisely to withdraw a number that should never have
    been a bar, so it must stop counting as one."""
    tag = autoresearch_cycle.slice_tag("hard", "reliability")
    _ledger(tmp_path, monkeypatch, [f"abc1234\t55.0\t0.0\tvoid\t{tag} baseline\n"])
    assert not autoresearch_cycle.has_baseline("hard", "reliability")


def test_the_batch_reports_an_idle_stop_in_its_exit_code():
    """Stopping quietly is what let a truncated batch become a score."""
    source = (ROOT / "tools" / "run_essays_batch.py").read_text(encoding="utf-8")
    assert "stopped_for_idle" in source
    assert "return 2 if stopped_for_idle else 0" in source


def test_the_cycle_refuses_a_batch_that_ran_fewer_documents_than_the_slice():
    """Checked independently of the exit code.

    A crash, a timeout, or an app that will not start all end a batch early and
    none of them says so in an exit code that reaches the cycle. The row count
    catches every one.
    """
    source = (ROOT / "tools" / "autoresearch_reliability_cycle.py").read_text(
        encoding="utf-8"
    )
    assert "measured < len(documents)" in source
    assert "_STOPPED_FOR_IDLE" in source
    assert reliability._STOPPED_FOR_IDLE == 2


def test_the_idle_gate_is_checked_once_and_not_per_document():
    """THE HARNESS ITSELF TYPES, so idle time cannot police a run from inside.

    It drives Word through SendInput and GetLastInputInfo counts synthetic input
    exactly like a person's - measured 2026-09-04, one synthetic VK_SHIFT takes
    idle from 308 seconds to 0. A per-document check therefore passes document
    one and refuses every document after it, permanently.

    That is not theory. The loop attempted a baseline 120 times over four hours,
    stopped after exactly one document every time, wrote nothing, and then
    refused the whole queue for having no baseline.
    """
    source = (ROOT / "tools" / "run_essays_batch.py").read_text(encoding="utf-8")
    assert "number == args.start" in source, (
        "the idle gate runs per document again, which no multi-document run can "
        "survive"
    )


def test_the_specification_records_why_the_gate_is_checked_once():
    """The next agent will find a per-document check obviously better, exactly
    as I did. The reason it cannot work has to be written down."""
    program = (ROOT / "program.md").read_text(encoding="utf-8")
    assert "CHECK IT ONCE, BEFORE THE RUN" in program
    assert "THE HARNESS ITSELF TYPES" in program


def test_a_harness_error_is_a_crash_not_a_zero(tmp_path, monkeypatch):
    """program.md lists the crash shapes - dead, partial, captured from
    'Claude', COMError - and says they "all mean no score rather than a low
    one".

    On 2026-09-06 all three documents of a slice came back outcome='error' with
    "the review on screen was captured from None", each after 17 to 21 seconds
    rather than the usual hundred. Nothing was measured, and the cycle scored
    the run 0.0 and recorded it as the slice baseline. That is the third time
    this loop turned a failed measurement into a number.
    """
    summary = tmp_path / "batch-summary.json"
    summary.write_text(
        json.dumps(
            [
                {"essay": "a", "outcome": "error", "error": "captured from None"},
                {"essay": "b", "outcome": "error", "error": "captured from None"},
                {"essay": "c", "outcome": "error", "error": "captured from None"},
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(reliability, "SUMMARY", summary)
    rate, reason = reliability.completion_rate()
    assert rate is None, "a run that measured nothing was given a score"
    assert "could not be measured" in reason


def test_a_genuine_incomplete_run_is_still_scored(tmp_path, monkeypatch):
    """The property that must survive. A document that ran and did not finish
    is a real product result and has to count against the score, or the metric
    would only ever see perfection."""
    summary = tmp_path / "batch-summary.json"
    summary.write_text(
        json.dumps(
            [
                {"essay": "a", "outcome": "complete", "presses": 5},
                {"essay": "b", "outcome": "partial", "presses": 2},
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(reliability, "SUMMARY", summary)
    rate, reason = reliability.completion_rate()
    assert rate is not None and reason == ""
    assert 0.0 <= rate <= 100.0


def test_the_capture_wait_is_not_shorter_than_the_hud_wait():
    """A capture that has not arrived before the HUD deadline will not produce
    a usable review either, so a shorter bound only invents failures."""
    source = (ROOT / "tools" / "run_essays_batch.py").read_text(encoding="utf-8")
    assert "_CAPTURE_WAIT_SECONDS = 40.0" in source
    assert "timeout: float = _CAPTURE_WAIT_SECONDS" in source
