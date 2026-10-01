"""A mismatch diagnostic that names one side of the mismatch is not one.

On 2026-09-08 entry 122 of the experiment queue crashed and the ledger recorded:

    the review on screen was captured from
    '701b21fad7f5-03_multi_level_outline  -  Compatibility Mode - Word', no

It ends at "no". The batch writes "captured from 'X', not from Y", and the
110 character cap in completion_rate landed inside that sentence: what survived
was the document the app HAD captured, and what was cut was the document it was
supposed to capture. Those two names are the entire content of the fault, and
only one of them was kept.

This is the same shape as the capture-path defect fixed earlier the same day.
The evidence was present and complete, and the code reading it threw half away.

Only broken[0] was reported as well, so three documents failing for three
different reasons showed one reason and looked like a single fault.
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _entry in (ROOT / "tools", ROOT / "src"):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

import autoresearch_reliability_cycle as reliability  # noqa: E402

# The real message, verbatim from the run that lost its tail.
CAPTURED = "701b21fad7f5-03_multi_level_outline  -  Compatibility Mode - Word"
REAL_ERROR = (
    f"the review on screen was captured from '{CAPTURED}', "
    f"not from 03_multi_level_outline"
)


def _summary(tmp_path, monkeypatch, rows):
    path = tmp_path / "batch-summary.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    monkeypatch.setattr(reliability, "SUMMARY", path)
    return path


def test_the_expected_document_survives_in_the_reason(tmp_path, monkeypatch):
    """Both halves of the mismatch, or it cannot be diagnosed."""
    _summary(
        tmp_path,
        monkeypatch,
        [
            {"essay": "a", "outcome": "error", "error": REAL_ERROR},
            {"essay": "b", "outcome": "complete", "presses": 5},
            {"essay": "c", "outcome": "complete", "presses": 5},
        ],
    )

    rate, reason = reliability.completion_rate()

    assert rate is None, "a harness failure was given a score"
    assert CAPTURED in reason, "the captured document is missing from the reason"
    assert "not from 03_multi_level_outline" in reason, (
        "The reason names what WAS captured and not what was expected, which is "
        "the half that makes it a mismatch. This is the truncation that "
        "recorded \"..., no\" on 2026-09-08."
    )


def test_distinct_reasons_are_all_reported(tmp_path, monkeypatch):
    """Three documents failing three ways showed one way."""
    _summary(
        tmp_path,
        monkeypatch,
        [
            {"essay": "a", "outcome": "error", "error": REAL_ERROR},
            {"essay": "b", "outcome": "error", "error": "the Writing HUD never appeared"},
            {"essay": "c", "outcome": "error", "error": "COMError -2146233083"},
        ],
    )

    _rate, reason = reliability.completion_rate()

    assert "not from 03_multi_level_outline" in reason
    assert "the Writing HUD never appeared" in reason
    assert "COMError" in reason


def test_one_reason_repeated_is_reported_once(tmp_path, monkeypatch):
    """Twenty documents failing identically must not print it twenty times."""
    _summary(
        tmp_path,
        monkeypatch,
        [{"essay": str(n), "outcome": "error", "error": REAL_ERROR} for n in range(20)],
    )

    _rate, reason = reliability.completion_rate()

    assert reason.count("not from 03_multi_level_outline") == 1
    assert "20 of 20 documents could not be measured" in reason


def test_many_distinct_reasons_are_bounded_without_cutting_one(tmp_path, monkeypatch):
    """Bounded by count, so whatever is shown is shown whole.

    The defect being fixed is a message cut mid-sentence. Replacing a character
    cap with a longer character cap would only move where it lands.
    """
    _summary(
        tmp_path,
        monkeypatch,
        [
            {"essay": str(n), "outcome": "error", "error": f"failure number {n} of a kind"}
            for n in range(9)
        ],
    )

    _rate, reason = reliability.completion_rate()

    for fragment in ("failure number 0 of a kind", "failure number 2 of a kind"):
        assert fragment in reason, "a shown reason was cut short"
    assert "further reasons" in reason, "the reasons that were dropped are not admitted"


def test_a_newline_in_a_reason_stays_on_one_line(tmp_path, monkeypatch):
    """A COM failure can carry a traceback, and a multi-line crash reason
    cannot be read back out of a log."""
    _summary(
        tmp_path,
        monkeypatch,
        [{"essay": "a", "outcome": "error", "error": "COMError\n  at line 3\n"}],
    )

    _rate, reason = reliability.completion_rate()

    assert "\n" not in reason
    assert "COMError at line 3" in reason
