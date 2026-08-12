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


def _in_focus(stub, incoming_handle: int | None) -> bool:
    incoming = SimpleNamespace(
        window=SimpleNamespace(handle=incoming_handle)
        if incoming_handle is not None
        else None
    )
    # The check consults the liveness probe, so the stub carries the real one.
    stub._hud_source_window_alive = lambda: _alive(stub)
    return window_module.EchoLinkWindow._hud_review_still_in_focus(stub, incoming)


def test_a_review_does_not_block_a_different_document(is_window):
    """The second half of the same bug, and the one that cost three essays.

    The source window was alive, so the gate kept the review and swallowed
    every focus signal from the document the writer had moved to. Replace was
    then refused against a document the review had never captured, and the
    counter read "0 of 0".
    """
    is_window(1)

    assert _in_focus(_stub(4851442), 9927001) is False


def test_a_review_still_blocks_signals_from_its_own_document(is_window):
    """Moving the caret inside the same document must not restart the review."""
    is_window(1)

    assert _in_focus(_stub(4851442), 4851442) is True


def test_a_dead_source_window_is_stale_whatever_has_focus(is_window):
    is_window(0)

    assert _in_focus(_stub(4851442), 4851442) is False


def test_an_unidentifiable_window_keeps_the_review(is_window):
    """Never discard a live review because a handle could not be read."""
    is_window(1)

    assert _in_focus(_stub(4851442), 0) is True
    assert _in_focus(_stub(4851442), None) is True
    assert _in_focus(_stub(0), 9927001) is True
    assert _in_focus(_stub(None), 9927001) is True
