"""A crash row that does not say why is a fact nobody can act on.

Of 26 crash rows in results.tsv on 2026-09-08, exactly one carried any hint of
a cause. The rest record 0.000000, the word "crash", and the experiment's
description. The reason was produced, printed to a console, and thrown away.

The cost is concrete and current. `tone Clear: baseline` crashed four separate
times, `tone Formal: baseline` four, `tone Friendly: baseline` six. Four of the
six palette tones have never produced a valid measurement, Gate 3 needs 95 per
cent on each of them, and the ledger cannot say whether that is one fault
happening fourteen times or several different ones - so there is nothing to
diagnose and no way to tell progress from coincidence.

This is the third time in two days that evidence was generated and discarded by
the code holding it: the harness read one of the app's two capture paths, the
reliability cycle cut a mismatch message in half, and every cycle threw away the
reason a run failed.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _entry in (ROOT / "tools", ROOT / "src"):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

import autoresearch_cycle  # noqa: E402


def test_the_reason_reaches_the_description():
    described = autoresearch_cycle.with_crash_reason(
        "tone Clear: baseline", "more than one pack served this run: ['a', 'b']"
    )
    assert "tone Clear: baseline" in described
    assert "more than one pack served this run" in described


def test_a_row_that_did_not_crash_is_untouched():
    """A keep or discard row keeps the exact shape every earlier row has."""
    assert (
        autoresearch_cycle.with_crash_reason("essays 8: an idea", None)
        == "essays 8: an idea"
    )
    assert (
        autoresearch_cycle.with_crash_reason("essays 8: an idea", "")
        == "essays 8: an idea"
    )


def test_tabs_and_newlines_never_reach_the_file():
    """results.tsv is tab separated and five columns wide.

    A reason carrying a tab would shift every field after it, corrupting rows
    that are already correct - so this is a property of the file format, not a
    tidiness preference.
    """
    described = autoresearch_cycle.with_crash_reason(
        "reliability open: an idea", "COMError\t-2146233083\n  at GetNextSibling\n"
    )
    assert "\t" not in described
    assert "\n" not in described
    assert "COMError -2146233083 at GetNextSibling" in described


def test_a_very_long_reason_is_cut_at_a_word():
    """Bounded, but never mid-token - the fault this whole change is about."""
    described = autoresearch_cycle.with_crash_reason(
        "reliability open: an idea", " ".join(["diagnostic"] * 100)
    )
    assert len(described) < 400
    assert described.endswith("[...]")
    assert "diagnosti[" not in described, "a word was cut in half"


def test_the_row_that_gets_written_carries_it(tmp_path, monkeypatch):
    """End to end through record(), because the description argument is the
    only channel a reason has into the file."""
    results = tmp_path / "results.tsv"
    monkeypatch.setattr(autoresearch_cycle, "RESULTS", results)

    autoresearch_cycle.record(
        "abc1234",
        None,
        0.0,
        "crash",
        autoresearch_cycle.with_crash_reason(
            "tone Clear: baseline", "the corrected document was never written"
        ),
    )

    row = results.read_text(encoding="utf-8").splitlines()[-1]
    fields = row.split("\t")
    assert len(fields) == 5, "the specification's five columns were not preserved"
    assert fields[1] == "0.000000" and fields[3] == "crash"
    assert "the corrected document was never written" in fields[4]
