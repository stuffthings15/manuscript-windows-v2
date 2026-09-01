"""A section marked requested with nothing in flight must be asked again.

Curtis reported a complete freeze: the review stopped two sections from the end
and never moved. The log said exactly what happened.

    01:15:30,786  section counter 20 of 22 -> 21 of 22
                  (completed=20 index=0 available=2 visible=2 total=22)
    01:15:30,789  Writing HUD advanced after Accept pending=2
                  [ nothing further, for nine hours ]

Published state at that moment: view=card, busy=false, provenance=waiting,
sections_ready 0 of 2 - and ai_completed=20 against ai_requested=16, so the
accounting could never satisfy itself.

The cause is a stale mark. A tone batch marks EVERY member requested up front,
and when the batch is discarded only some paths clear those marks - the failure
path clears members[1:] and leaves the first. Any member left marked can never be
requested again, because _request_ai_proofread_current refuses an id already in
_ai_requested_ids, so the card waits on work nobody asked for.

This is the third hang of this family: 2026-08-20 (a duplicate Replace lock) and
2026-08-23 (the HUD stranded in the loading view) were the first two. Chasing the
paths one at a time has not worked, so the mark now counts as evidence only while
a request is genuinely in flight.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


def _palette():
    """A palette instance with only the fields these paths touch."""
    from echo_link_app.rewrite_palette import RewritePalette

    palette = RewritePalette.__new__(RewritePalette)
    palette._ai_attempts = {}
    palette._ai_batch_members = {}
    palette._active_ai_attempt_id = 0
    return palette


def test_a_live_batch_counts_as_in_flight():
    """While the batch runs, the mark is real and must be respected.

    Without this the fix would re-request sections that are already being
    answered, doubling the work and racing two answers into one card.
    """
    palette = _palette()
    palette._ai_attempts = {7: "sentence:aaa"}
    palette._ai_batch_members = {7: ("sentence:aaa", "sentence:bbb")}

    assert palette._ai_request_in_flight("sentence:aaa") is True
    # A sibling in the same batch is equally in flight: the batch answers for
    # every member, not only the one whose card is showing.
    assert palette._ai_request_in_flight("sentence:bbb") is True


def test_a_finished_batch_leaves_nothing_in_flight():
    """The deadlock state. The attempt is gone; the members were left marked."""
    palette = _palette()
    palette._ai_attempts = {}
    palette._ai_batch_members = {}

    assert palette._ai_request_in_flight("sentence:bbb") is False


def test_a_discarded_attempt_with_members_still_recorded_is_not_in_flight():
    """The exact shape the failure path leaves behind.

    `_ai_batch_members` is popped on some paths and not on others, so members can
    outlive their attempt. An entry whose attempt is no longer live must not read
    as in flight, or the card waits on an answer that will never come.
    """
    palette = _palette()
    palette._ai_attempts = {}
    palette._ai_batch_members = {7: ("sentence:aaa", "sentence:bbb")}
    palette._active_ai_attempt_id = 0

    assert palette._ai_request_in_flight("sentence:bbb") is False


def test_the_active_attempt_counts_even_without_batch_members():
    """A single-section request records no batch, only the attempt."""
    palette = _palette()
    palette._active_ai_attempt_id = 9
    palette._ai_attempts = {9: "sentence:ccc"}

    assert palette._ai_request_in_flight("sentence:ccc") is True
    assert palette._ai_request_in_flight("sentence:ddd") is False
