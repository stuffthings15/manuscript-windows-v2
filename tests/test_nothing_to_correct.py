"""Clicking the badge on a finished document must say so, not vanish.

Curtis reported this three times on 2026-09-01, the last time as "you click
the small icon and it just disappears and does not come back", and asked how a
problem that had already been fixed kept coming back. It had not been fixed: it
was diagnosed and left open.

The published state named the cause once the decline reason was logged:

    section counter start -> 1 of 9 (available=9)
    made no AI request: there is no current section (id=none index=0 sections=0
                                                    view=loading)

Eighteen milliseconds after offering nine sections the review held none. Every
section had been through the AI and come back unchanged, because the document
was already corrected, so all nine were filtered out - and `_open_from_badge`
fell through to a card with nothing in it. The empty review is deliberate. The
missing part was any answer to the click.
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


def _finished_document(palette, app, text="One. Two."):
    """Take a document all the way through the AI with no change to make."""
    requests = []
    palette.sentence_rewrite_requested.connect(
        lambda attempt_id, suggestion_id, sent, command: requests.append(
            (attempt_id, suggestion_id, sent, command)
        )
    )
    palette.show_capture(text)
    app.processEvents()
    palette.badge_button.click()
    # Answer every request with the text exactly as it was sent.
    seen = 0
    while len(requests) > seen:
        attempt_id, suggestion_id, sent, _command = requests[seen]
        seen += 1
        palette.show_sentence_result(attempt_id, suggestion_id, sent, "Ready.")
        app.processEvents()
    return requests


def test_badge_click_on_a_finished_document_says_so(app):
    palette = RewritePalette(ai_proofread=True)
    _finished_document(palette, app)

    palette.badge_button.click()
    app.processEvents()

    # The badge is still there, which is the only way back into the document,
    # and the writer is told why nothing opened.
    assert palette._view == "badge"
    assert "Nothing left to correct" in palette.status.text()


def test_a_finished_document_never_opens_an_empty_card(app):
    palette = RewritePalette(ai_proofread=True)
    _finished_document(palette, app)

    palette.badge_button.click()
    app.processEvents()

    # The card view with no sections is the state that read as a crash.
    assert not (palette._view == "card" and not palette._review_suggestions)
