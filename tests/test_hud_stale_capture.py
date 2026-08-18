"""The Writing HUD must not serve one document's review to another.

A review left in card view blocked every later focus signal, so when the
source document closed and a new one opened, the new document inherited the
old review: an eleven-section job was shown for a ten-section document and its
counter read "4 of 11" for text it had never seen. The gate now asks whether
the review on screen still has a document behind it.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from echo_link_app import window as window_module


def _stub(handle: int | None):
    """A window object carrying only what the check reads."""
    if handle is None:
        return SimpleNamespace(selected_text_record=None)
    return SimpleNamespace(
        selected_text_record=SimpleNamespace(window=SimpleNamespace(handle=handle))
    )


def _alive(stub) -> bool:
    return window_module.EchoLinkWindow._hud_source_window_alive(stub)


@pytest.fixture
def is_window(monkeypatch):
    """Control what Windows reports for a handle."""

    def _set(result: int):
        monkeypatch.setattr(
            window_module.ctypes,
            "windll",
            SimpleNamespace(user32=SimpleNamespace(IsWindow=lambda _handle: result)),
        )

    return _set


def test_a_review_whose_source_window_closed_is_not_alive(is_window):
    """The whole bug: this returning True discarded the next document."""
    is_window(0)
    assert _alive(_stub(4851442)) is False


def test_a_review_with_a_living_source_window_is_kept(is_window):
    is_window(1)
    assert _alive(_stub(4851442)) is True


def test_no_capture_at_all_keeps_the_existing_behaviour():
    assert _alive(_stub(None)) is True


def test_a_record_without_a_window_handle_is_never_discarded():
    """Synthetic records have no handle and must not close a live review."""
    assert _alive(_stub(0)) is True


def test_a_failing_probe_never_discards_a_review(monkeypatch):
    """Guessing wrong here throws away a review the reader is working in."""

    def explode(_handle):
        raise OSError("user32 unavailable")

    monkeypatch.setattr(
        window_module.ctypes,
        "windll",
        SimpleNamespace(user32=SimpleNamespace(IsWindow=explode)),
    )
    assert _alive(_stub(4851442)) is True


# The handle comparison that used to live here was removed on 2026-08-15, and
# the four tests that pinned it went with it. It asked whether the focused
# window WAS the review's window and let every other window through, which is
# right for a second document and wrong for every other window on the machine.
# What replaces it is below. The case it protected - moving to a second
# document - is now served by the writer pressing the shortcut, which reads a
# fresh capture and replaces the review.


# The other half of the same gate, decided by Curtis on 2026-08-15.
#
# The check above briefly also asked whether the focused window WAS the
# review's window, letting every other window through so the writer could move
# to a second document. That is right for a second document and wrong for every
# other window on the machine: in ten minutes the HUD captured the chat
# application eleven times and the essay it was correcting twice, rebuilding the
# review each time. A writer sees that as the card spamming them on every click.
#
# A focus signal is the machine noticing, not the writer asking, so it may not
# take a review off the screen. Moving to a second document is served by the
# shortcut, which reads a fresh capture and replaces the review.


def _detected(
    handle: int,
    text: str = "some text a writer selected",
    *,
    scope: str = "selection",
):
    from echo_link_core.windows_selection import DetectedSelection
    from echo_link_core.insertion import SelectionRecord, WindowSnapshot

    return DetectedSelection(
        record=SelectionRecord(
            text, WindowSnapshot(handle, 4242, "Another window"), scope=scope
        ),
        anchor=(0, 0, 10, 10),
    )


def _window_showing(view: str, *, automatic: bool, source_handle: int):
    """A stub window whose only real answers are the ones the gate reads.

    A mock stands in for the rest of the window because the handler runs on
    past the decision into badge timers and status labels, and none of that is
    what these tests are about. The assertion is behavioural either way: did a
    focus signal put a new capture on screen, or did it leave the review alone.
    """
    from unittest.mock import MagicMock

    stub = MagicMock()
    stub.selection_monitor = None
    stub.rewrite_palette.isVisible.return_value = True
    stub.rewrite_palette.view = view
    stub.rewrite_palette.automatic = automatic
    # A real record, because the handler reads more of it than a stub would
    # suggest: the scope and the control it came from, not only the handle.
    stub.selected_text_record = _detected(source_handle, "the review on screen").record
    stub.engine.settings.automatic_writing_hud = True
    stub.engine.is_busy = False
    stub._quitting = False
    stub._automatic_hud_suppress_until = 0.0
    stub._hud_source_window_alive.return_value = True
    stub._badge_clear_timer = None
    # The real method, not a mock's truthy stand-in. This one decides whether a
    # capture is a different DOCUMENT, and a MagicMock answers every question
    # yes, which silently turned "a selection in another window" into "the
    # writer opened another document" and made the gate look broken.
    stub._is_a_different_document = (
        lambda record: window_module.EchoLinkWindow._is_a_different_document(stub, record)
    )
    return stub


def _showed_a_capture(stub) -> bool:
    return stub.rewrite_palette.show_capture.called


@pytest.fixture
def quiet_status(monkeypatch):
    """Past the decision the handler announces to a real Qt accessible object.

    A mock is not one, so the announcement raises and hides the result these
    tests are about. Silencing it leaves the decision itself untouched.
    """
    monkeypatch.setattr(window_module, "update_status_label", lambda *a, **k: None)


def test_a_click_into_another_window_does_not_rebuild_an_open_review(is_window):
    """The card the writer is reading survives them clicking somewhere else."""
    is_window(1)
    stub = _window_showing("card", automatic=True, source_handle=4851442)

    window_module.EchoLinkWindow._handle_automatic_selection(stub, _detected(9927001))

    assert _showed_a_capture(stub) is False


def test_opening_another_document_does_replace_an_open_review(is_window, quiet_status):
    """A different document is the writer moving on, not a stray click.

    The test above stays and is the case that matters: a selection typed into
    some other window may not take a review off the screen. This is the other
    half. On 2026-08-18 a writer left document 1 at 9 of 10, opened document 2,
    and got nothing at all. The distinguishing fact, taken from that day's log,
    is scope: every spurious capture was a selection inside a chat box, a
    browser field or a search box, and every real document was a whole-document
    capture from its own window.
    """
    is_window(1)
    stub = _window_showing("card", automatic=True, source_handle=4851442)

    window_module.EchoLinkWindow._handle_automatic_selection(
        stub, _detected(9927001, scope="document")
    )

    assert _showed_a_capture(stub) is True


def test_a_badge_is_still_replaced_by_a_new_capture(is_window, quiet_status):
    """Only a review the writer is working in is protected, not a bare badge."""
    is_window(1)
    stub = _window_showing("badge", automatic=True, source_handle=4851442)

    window_module.EchoLinkWindow._handle_automatic_selection(stub, _detected(9927001))

    assert _showed_a_capture(stub) is True


def test_a_review_whose_document_has_closed_stops_blocking(is_window, quiet_status):
    """A card with no document behind it must not block the machine forever."""
    is_window(1)
    stub = _window_showing("card", automatic=True, source_handle=4851442)
    stub._hud_source_window_alive.return_value = False

    window_module.EchoLinkWindow._handle_automatic_selection(stub, _detected(9927001))

    assert _showed_a_capture(stub) is True
