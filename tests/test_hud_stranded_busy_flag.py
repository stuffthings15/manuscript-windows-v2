"""A busy flag nobody is going to clear must not end the review.

THE FOURTH FREEZE traced to `_request_ai_proofread_current`, and the first that
was not a stale request mark. Measured 2026-09-02 during a Shorten tone
baseline: the review applied 23 of 25 sections cleanly, each logging `Replace
Text invoked busy=False` and then an `adaptive Polish request`. After the
twenty-third, busy went true, NO request was ever logged, and it sat at
"24 of 25" with two sections pending until it was killed twelve minutes later.

    19:14:54  section accepted ... pending 3 -> 2
    19:14:57  made no AI request: the HUD is busy (index=0 sections=2 view=card)

`_start_sentence_ai_request` sets busy and THEN emits the request. Every path
that clears busy again depends on an answer - a result, an error, a timeout, a
cancel - so a request the window declines silently strands the flag forever.
"""
from __future__ import annotations

import os

import pytest
from PySide6.QtWidgets import QApplication

from echo_link_app.rewrite_palette import RewritePalette
from echo_link_app.theme import apply_dark_theme


@pytest.fixture(scope="module")
def app():
    os.environ["ECHOLINK_DISABLE_SOUNDS"] = "1"
    os.environ["ECHOLINK_DISABLE_MEDIA_CONTROL"] = "1"
    application = QApplication.instance() or QApplication([])
    application.setStyle("Fusion")
    apply_dark_theme(application)
    return application


def _review(app, palette, text="One. Two. Three."):
    palette.show_capture(text)
    app.processEvents()
    palette.badge_button.click()
    app.processEvents()


def test_set_busy_records_who_owns_the_flag(app):
    palette = RewritePalette(ai_proofread=True)
    assert palette._busy_command == ""

    palette.set_busy(True, "AI proofread")
    assert palette._busy_command == "AI proofread"

    palette.set_busy(False)
    assert palette._busy_command == ""


def test_a_busy_flag_with_nothing_in_flight_is_stale(app):
    palette = RewritePalette(ai_proofread=True)
    palette.set_busy(True, "AI proofread")
    palette._active_ai_attempt_id = 0
    palette._ai_attempts.clear()

    assert "nothing is in flight" in palette._stale_busy_reason()


def test_a_busy_flag_with_a_request_in_flight_is_not_stale(app):
    palette = RewritePalette(ai_proofread=True)
    palette.set_busy(True, "AI proofread")
    palette._active_ai_attempt_id = 7

    assert palette._stale_busy_reason() == ""


def test_a_pending_pool_attempt_also_counts_as_in_flight(app):
    palette = RewritePalette(ai_proofread=True)
    palette.set_busy(True, "AI proofread")
    palette._active_ai_attempt_id = 0
    palette._ai_attempts[9] = "sentence:abc"

    assert palette._stale_busy_reason() == ""


def test_a_non_ai_command_keeps_the_flag(app):
    """Translate owns busy through a different path with no attempt id.

    Clearing that would abandon work the writer asked for.
    """
    palette = RewritePalette(ai_proofread=True)
    palette.set_busy(True, "Translate")
    palette._active_ai_attempt_id = 0
    palette._ai_attempts.clear()

    assert palette._stale_busy_reason() == ""


def test_a_review_recovers_from_a_stranded_flag(app):
    """The behaviour that matters: the next section is requested anyway."""
    palette = RewritePalette(ai_proofread=True)
    requests = []
    palette.sentence_rewrite_requested.connect(
        lambda attempt, suggestion, text, command: requests.append(suggestion)
    )
    _review(app, palette)
    requests.clear()

    # Strand the flag exactly as the window declining a request would.
    palette.set_busy(True, "AI proofread")
    palette._active_ai_attempt_id = 0
    palette._ai_attempts.clear()

    asked = palette._request_ai_proofread_current()

    assert asked is True, "the review did not recover and ask for the section"
    assert requests, "no AI request was emitted after recovery"
    # Busy is true again, and correctly so: a fresh request is now in flight.
    # What matters is that it is no longer STRANDED, which is the difference
    # between a review that is working and one that is finished for good.
    assert palette._stale_busy_reason() == "", "the flag is stranded again"
