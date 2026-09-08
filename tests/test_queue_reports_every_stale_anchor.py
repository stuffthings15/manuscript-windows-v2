"""A kept experiment breaks the entries anchored behind it. Find them together.

This has now stopped a queue run twice. `apply` records the first: entry 1
raised the tie-break ratio and was kept, so a later entry could no longer find
`= 50` and the run ended with two experiments unrun.

The second was 2026-09-08. Entry 64 of 212 was kept - it moved
_COMPOUND_MINIMUM_EDIT_SEPARATION from 2 to 1, taking essay 8 from 98.0 to
100.0, which is a real improvement and exactly what the loop is for. Nine later
entries were still anchored to `= 2`. The queue halts on a bad anchor rather
than skipping it, deliberately and for a good reason, so those nine are nine
halts and nine restarts, each one arriving after the queue has ground through
however many experiments separate it from the last.

Found together they are a single edit. That is the whole of this rule.

It cannot see drift a keep will cause LATER in the same run; only the tree as
it stands. That is the accumulated drift of every previous run, and it is the
case that has actually cost the time.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _entry in (ROOT / "tools", ROOT / "src"):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

import autoresearch_queue as queue_module  # noqa: E402


def _entry_anchored_to(text_before: str, text_after: str):
    """A queue entry shaped like a real one, pointed at a file we control."""
    return ("a described idea", "target.py", ((text_before, text_after),), "8")


def test_a_stale_anchor_is_reported(tmp_path, monkeypatch):
    """The exact shape that halted the run: neither form is in the file."""
    (tmp_path / "target.py").write_text("SEPARATION = 1\n", encoding="utf-8")
    monkeypatch.setattr(
        queue_module, "QUEUE", [_entry_anchored_to("SEPARATION = 2", "SEPARATION = 3")]
    )
    monkeypatch.setattr(queue_module, "already_measured", lambda *_a: False)

    stale = queue_module.stale_anchors([0], tmp_path)

    assert len(stale) == 1, (
        "The pre-flight cannot see the defect it exists to catch. An anchor "
        "matching neither form is what halts the queue mid-run."
    )
    index, description, where = stale[0]
    assert index == 0
    assert "SEPARATION = 2" in where, "the report has to name the text that is missing"


def test_an_anchor_that_resolves_is_not_reported(tmp_path, monkeypatch):
    """A rule that fires on healthy entries would stop every run instead."""
    (tmp_path / "target.py").write_text("SEPARATION = 1\n", encoding="utf-8")
    monkeypatch.setattr(
        queue_module, "QUEUE", [_entry_anchored_to("SEPARATION = 1", "SEPARATION = 3")]
    )
    monkeypatch.setattr(queue_module, "already_measured", lambda *_a: False)

    assert queue_module.stale_anchors([0], tmp_path) == []


def test_an_edit_already_applied_is_not_stale(tmp_path, monkeypatch):
    """`apply` treats an already-present result as success, so this must too.

    A combination entry follows its own parts by construction, so the after
    form being in the file is the normal case, not an error. Reporting it would
    refuse to start a queue that would have run perfectly.
    """
    (tmp_path / "target.py").write_text("SEPARATION = 3\n", encoding="utf-8")
    monkeypatch.setattr(
        queue_module, "QUEUE", [_entry_anchored_to("SEPARATION = 1", "SEPARATION = 3")]
    )
    monkeypatch.setattr(queue_module, "already_measured", lambda *_a: False)

    assert queue_module.stale_anchors([0], tmp_path) == []


def test_a_measured_entry_needs_no_anchor(tmp_path, monkeypatch):
    """It will be skipped before apply() ever sees it, so it cannot halt anything."""
    (tmp_path / "target.py").write_text("SEPARATION = 1\n", encoding="utf-8")
    monkeypatch.setattr(
        queue_module, "QUEUE", [_entry_anchored_to("SEPARATION = 2", "SEPARATION = 3")]
    )
    monkeypatch.setattr(queue_module, "already_measured", lambda *_a: True)

    assert queue_module.stale_anchors([0], tmp_path) == []


def test_every_anchor_in_the_real_queue_resolves_against_the_real_tree():
    """The live check, which is the one that matters.

    If this fails, the queue will halt partway through its next run. Re-point
    the named entries at what the file says now - the target value on the right
    of each pair still means what the entry's description says, because these
    experiments set an absolute value rather than a relative one.
    """
    stale = queue_module.stale_anchors(range(len(queue_module.QUEUE)), ROOT)
    assert not stale, "stale queue anchors:\n" + "\n".join(
        f"  {index}: {description}\n     {where}" for index, description, where in stale
    )
