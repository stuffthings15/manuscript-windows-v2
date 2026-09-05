"""A caret that moves inside an unchanged document must not rebuild the review.

THE NON-DETERMINISM THAT MADE THE RELIABILITY METRIC MEANINGLESS.

Measured across 98 automated runs of two documents, 2026-09-04 and 05. Twenty-two
of them accepted NOTHING at all, and the separation from the successful runs is
total:

    succeeded (76): the next focus signal arrived 22.6s to 57.5s after capture
    failed    (22): it arrived 3.3s to 4.7s after capture

In all 22 failures the replacing capture was the same size as the one it
replaced, and in all 22 the caret anchor had moved - x from 801 to 915 or 993,
y identical. Nothing had been typed.

The duplicate-signal guard compared whole SelectionRecords, and a record carries
the caret anchor. So an identical document reported again with the caret
slightly further along compared unequal, the guard did not fire, and a prepared
review was thrown away before a single section could be accepted.

That is why the same corpus and the same product code baselined at 66.7 on one
run and 33.3 on the next, and why 36 experiments discarded at 33.3 against a bar
of 66.7. The metric was measuring a coin flip, and every one of those discards
was noise rather than a refuted idea.
"""
from __future__ import annotations

import hashlib

from echo_link_core.insertion import SelectionRecord, WindowSnapshot
from echo_link_app.window import _is_the_same_capture

DOCUMENT = (
    "Mixed Lists\n"
    "Numbered items followed by bulleted ones .\n"
    "1. The first numbered item have an error.\n"
    "The second numbered item is fine.\n"
)
DIGEST = hashlib.sha256(DOCUMENT.encode("utf-8")).hexdigest()
WINDOW = WindowSnapshot(4242, 99, "11_mixed_lists - Word", "OpusApp")


def _record(text=DOCUMENT, *, anchor=(801, 761, 802, 791), window=WINDOW,
            scope="document", digest=DIGEST):
    return SelectionRecord(
        text,
        window,
        scope,
        digest,
        (),
        anchor,
    )


def test_a_moved_caret_is_the_same_capture():
    """The exact failure: 801 to 915, y unchanged, nothing typed."""
    assert _is_the_same_capture(
        _record(anchor=(801, 761, 802, 791)),
        _record(anchor=(915, 761, 916, 791)),
    )


def test_the_other_observed_caret_move_too():
    """The other x seen in the logs, so the fixture is the measurement."""
    assert _is_the_same_capture(
        _record(anchor=(801, 761, 802, 791)),
        _record(anchor=(993, 761, 994, 791)),
    )


def test_edited_text_is_a_new_capture():
    """The property that must survive. A document the writer changes has to be
    offered again, or corrections would be applied against stale text - which
    is the defect fixed in 038bd4a, arrived at from the other side."""
    edited = DOCUMENT.replace("have an error", "has an error")
    assert not _is_the_same_capture(
        _record(),
        _record(edited, digest=hashlib.sha256(edited.encode("utf-8")).hexdigest()),
    )


def test_a_different_digest_is_a_new_capture():
    """Same length, different content. Text equality alone could be fooled by a
    reader that truncates, so the digest decides when both carry one."""
    assert not _is_the_same_capture(_record(), _record(digest="f" * 64))


def test_a_different_window_is_a_new_capture():
    """Another document in another window is never the same work."""
    other = WindowSnapshot(777, 99, "12_restarted_numbering - Word", "OpusApp")
    assert not _is_the_same_capture(_record(), _record(window=other))


def test_a_different_process_is_a_new_capture():
    """Two Word instances can show documents with identical text."""
    other = WindowSnapshot(4242, 1234, "11_mixed_lists - Word", "OpusApp")
    assert not _is_the_same_capture(_record(), _record(window=other))


def test_a_selection_is_not_the_same_as_the_whole_document():
    """Scope decides how every offset downstream is read."""
    assert not _is_the_same_capture(_record(), _record(scope="selection"))


def test_nothing_in_hand_is_never_the_same_capture():
    """No review to keep means the signal must be acted on."""
    assert not _is_the_same_capture(None, _record())
    assert not _is_the_same_capture(_record(), None)


def test_missing_digests_fall_back_to_the_text():
    """A selection capture may carry no digest at all. It must still dedupe on
    what it does have rather than always rebuilding."""
    assert _is_the_same_capture(
        _record(scope="selection", digest="", anchor=(801, 761, 802, 791)),
        _record(scope="selection", digest="", anchor=(915, 761, 916, 791)),
    )


def test_the_handler_refreshes_the_record_without_rebuilding():
    """The badge follows the caret, which is the only thing the anchor is for.

    Keeping the old record would pin the badge to where the caret used to be,
    so the fix updates the stored record and returns rather than returning with
    the stale one.
    """
    source = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "src" / "echo_link_app" / "window.py"
    ).read_text(encoding="utf-8")
    guard = source.split("unchanged = _is_the_same_capture(", 1)[1]
    guard = guard.split("self.selected_text_record = record\n        self.writing_source", 1)[0]
    assert "self.selected_text_record = record" in guard, (
        "the record is not refreshed, so the badge would stay at the old caret"
    )
