"""Every runnable queue entry must still match the source it edits.

The queue applies a literal substitution and raises SystemExit when the text
matches neither the before nor the after form - deliberately, because "a bad
anchor makes every later entry suspect". That is the right behaviour and it is
also a landmine: one rotted anchor stops the whole night at the entry it reaches,
hours after anybody could notice.

This finds them in a tenth of a second instead.

ENTRIES 0 TO 11 ARE EXEMPT AND THAT IS NOT A LOOPHOLE. They have all been run
and their rows are in results.tsv, and several targeted constants that LATER
kept experiments moved again: entry 2 narrows _MARGIN from 0.02 to 0.012, and a
subsequent kept experiment took it to 0.008, so its anchor can no longer match
anything. They are a record of what was tried, not work still to do. Re-running
them would repeat measurements that have already been made.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import autoresearch_queue  # noqa: E402

FIRST_RUNNABLE = 12


def test_every_runnable_entry_can_still_be_applied():
    broken = []
    for index, entry in enumerate(autoresearch_queue.QUEUE):
        if index < FIRST_RUNNABLE:
            continue
        text = (ROOT / entry[1]).read_text(encoding="utf-8")
        for before, after in entry[2]:
            if before not in text and after not in text:
                broken.append(f"{index}: {before!r} in {entry[1]}")
    assert not broken, "these entries would stop the queue:\n" + "\n".join(broken)


def test_every_runnable_entry_names_a_slice_and_a_kind():
    for index, entry in enumerate(autoresearch_queue.QUEUE):
        if index < FIRST_RUNNABLE:
            continue
        assert entry[3], f"entry {index} names no slice"
        kind = entry[4] if len(entry) > 4 else "essays"
        assert kind in {"essays", "tone", "reliability"} or kind.startswith(
            "essays-"
        ), f"entry {index} has kind {kind!r}, which the queue cannot dispatch"


def test_quality_experiments_only_target_slices_with_headroom():
    """Nine essay slices sit at exactly 100. The keep rule is a strict
    improvement, so an experiment there is a guaranteed discard that costs five
    minutes and answers nothing. The spec says the same thing about essay 1."""
    allowed = {"8", "12", "17"}
    for index, entry in enumerate(autoresearch_queue.QUEUE):
        if index < FIRST_RUNNABLE:
            continue
        kind = entry[4] if len(entry) > 4 else "essays"
        if kind == "essays":
            assert entry[3] in allowed, (
                f"entry {index} targets essay {entry[3]}, which is already at 100"
            )


def test_a_measured_entry_is_recognised_and_an_unmeasured_one_is_not(
    tmp_path, monkeypatch
):
    """Curtis, 2026-09-03: we should not repeat experiments.

    This test used to assert that no queued entry appeared in results.tsv, which
    was true only until the queue ran. Eleven entries were measured on
    2026-09-04 and it then failed for exactly the right reason: restarting at
    the same index would have re-measured all eleven, an hour spent writing rows
    that already existed.

    A queue gets restarted often - a night is interrupted, a defect is fixed,
    the machine is wanted back - so the queue itself now skips what the ledger
    holds, and the property worth asserting is that skipping rather than the
    absence of history.
    """
    import autoresearch_cycle

    tag = autoresearch_cycle.slice_tag("hard", "reliability")
    ledger = tmp_path / "results.tsv"
    ledger.write_text(
        "commit\tval\tmem\tstatus\tdescription\n"
        f"abc1234\t0.0\t0.0\tdiscard\t{tag} an idea that was tried\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(autoresearch_cycle, "RESULTS", ledger)

    assert autoresearch_queue.already_measured(
        "an idea that was tried", "hard", "reliability"
    )
    assert not autoresearch_queue.already_measured(
        "an idea nobody has tried", "hard", "reliability"
    )
    # The same idea on a different slice is a different experiment.
    assert not autoresearch_queue.already_measured(
        "an idea that was tried", "8", "essays"
    )


def test_a_voided_row_does_not_count_as_measured(tmp_path, monkeypatch):
    """Voiding withdraws a number precisely because it was not a measurement,
    so the experiment still needs running."""
    import autoresearch_cycle

    tag = autoresearch_cycle.slice_tag("hard", "reliability")
    ledger = tmp_path / "results.tsv"
    ledger.write_text(
        "commit\tval\tmem\tstatus\tdescription\n"
        f"abc1234\t0.0\t0.0\tvoid\t{tag} an idea whose run was thrown away\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(autoresearch_cycle, "RESULTS", ledger)
    assert not autoresearch_queue.already_measured(
        "an idea whose run was thrown away", "hard", "reliability"
    )


def test_the_queue_skips_rather_than_repeats():
    """The live ledger: eleven entries are measured, and the queue must not
    offer them again."""
    source = (ROOT / "tools" / "autoresearch_queue.py").read_text(encoding="utf-8")
    assert "already_measured(description, slice_name, kind)" in source
    assert "already measured" in source
