"""What the release gate counts as a run that did not finish.

Changed on 2026-08-30 at Curtis's instruction. The test was
`outcome != "complete"`, and `run_essays_batch.outcome` returns "partial"
whenever `unchanged` is non-empty - but `unchanged` counts paragraphs the review
LEFT ALONE, which is a quality signal, not a completion signal. A tone may
legitimately have nothing to say about a sentence.

The rows below are real, taken from runs on 2026-08-29 and 2026-08-30, because
an invented row proves the function agrees with my idea of the format rather
than with the format.

The risk in this change is loosening a gate by accident, so most of these assert
that something is STILL excluded.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from release_acceptance import _run_was_truncated  # noqa: E402


def test_a_fully_applied_run_with_an_unchanged_paragraph_counts_as_complete():
    """The case this change exists for. Essay 3, tone Improve, 2026-08-30.

    All 22 of 22 sections pressed, no stall, no error, one paragraph unchanged.
    It was excluded, and excluding it made the whole Improve tone unmeasured.
    """
    row = {
        "essay": "03_the_promise_and_limits_of_solar_microgrids",
        "outcome": "partial",
        "presses": 22,
        "stalled_at": None,
        "unchanged": [1],
        "stopped_because": (
            "the HUD could not be found within 8s after press 22; section 22/22, "
            "ready 1, refused 0, view=card, replace_enabled=True, tone=Improve"
        ),
        "error": None,
    }
    assert _run_was_truncated(row) is False


def test_a_run_that_stopped_mid_document_is_still_excluded():
    """Essay 2, tone Improve, before the HUD fix. 24 of 25 is not finished."""
    row = {
        "essay": "02_how_public_libraries_strengthen_communities",
        "outcome": "partial",
        "presses": 23,
        "stalled_at": None,
        "stopped_because": (
            "no Replace button at press 24; section 24/25, ready 1, refused 0, "
            "view=card, replace_enabled=True, tone=Improve"
        ),
        "error": None,
    }
    assert _run_was_truncated(row) is True


def test_the_essay_that_died_on_its_first_section_is_still_excluded():
    """Essay 17 in the full tier. section 1/21, and it blocked the whole gate."""
    row = {
        "essay": "17_what_ancient_roads_reveal_to_archaeologists",
        "outcome": "dead",
        "presses": 0,
        "stalled_at": None,
        "stopped_because": "no Replace button at press 1; section 1/21, ready 20",
        "error": None,
    }
    assert _run_was_truncated(row) is True


@pytest.mark.parametrize(
    "row",
    [
        pytest.param(
            {"presses": 12, "error": "COMError: (-2146233083, None, ...)"},
            id="an error is still excluded",
        ),
        pytest.param(
            {"presses": 0, "error": None},
            id="no presses is still excluded",
        ),
        pytest.param(
            {"presses": 14, "stalled_at": "1 of 25", "error": None},
            id="a stall is still excluded",
        ),
    ],
)
def test_the_other_exclusions_are_untouched(row):
    """The three signals that always meant a broken run still mean it.

    This change was meant to correct one conflation, not to widen the gate, and
    these are the ways widening it would show up.
    """
    assert _run_was_truncated(row) is True


def test_a_run_with_no_progress_marker_and_nothing_wrong_is_complete():
    """A clean run records no stopped_because at all."""
    row = {"presses": 25, "stalled_at": None, "stopped_because": None, "error": None}
    assert _run_was_truncated(row) is False


def test_the_old_rule_would_have_disagreed_on_the_case_that_matters():
    """Proof the change is not a no-op, which is the other way this could fail.

    If this ever passes trivially, the fix has been reverted somewhere and the
    tone gates go quietly back to reporting nothing.
    """
    row = {
        "outcome": "partial",
        "presses": 22,
        "stalled_at": None,
        "unchanged": [1],
        "stopped_because": "the HUD could not be found within 8s after press 22; section 22/22",
        "error": None,
    }
    old_rule_says_incomplete = str(row.get("outcome") or "") != "complete"
    assert old_rule_says_incomplete is True
    assert _run_was_truncated(row) is False
