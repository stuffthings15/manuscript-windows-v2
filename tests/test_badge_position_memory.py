"""The badge remembers where it was dragged, relative to the text.

Curtis, 2026-09-01: "you should always be able to move it, and the system
should remember wherever you move it in relation to the text AUTOMATICALLY."

Relative to the text, never to the screen. A screen position is wrong as soon
as the document is scrolled, the window is moved, or the writer uses a second
monitor - the badge would sit where the text used to be.
"""
from __future__ import annotations

import os

import pytest
from PySide6.QtWidgets import QApplication

from echo_link_app.rewrite_palette import RewritePalette, _anchor_in_screen_points
from echo_link_app.theme import apply_dark_theme
from echo_link_core.models import DictationSettings


@pytest.fixture(scope="module")
def app():
    os.environ["ECHOLINK_DISABLE_SOUNDS"] = "1"
    os.environ["ECHOLINK_DISABLE_MEDIA_CONTROL"] = "1"
    application = QApplication.instance() or QApplication([])
    application.setStyle("Fusion")
    apply_dark_theme(application)
    return application


def test_settings_carry_no_offset_until_the_badge_is_dragged():
    settings = DictationSettings()
    assert settings.hud_badge_offset_x is None
    assert settings.hud_badge_offset_y is None
    assert settings.hud_badge_position == "above-right"


def test_a_remembered_offset_is_applied_from_the_anchor(app):
    palette = RewritePalette()
    anchor = (100, 200, 400, 260)
    screen = QApplication.primaryScreen()
    _left, top, right, _bottom = _anchor_in_screen_points(anchor, screen)

    palette.set_badge_offset((25, -40))
    palette._position_on_screen(anchor=anchor)

    # Measured from the anchor's right edge and top, which is the same corner
    # the default placement uses.
    assert palette.x() == right + 25
    assert palette.y() == top - 40


def test_clearing_the_offset_returns_to_the_default_placement(app):
    palette = RewritePalette()
    anchor = (100, 400, 400, 460)
    screen = QApplication.primaryScreen()
    _left, top, right, _bottom = _anchor_in_screen_points(anchor, screen)

    palette.set_badge_offset(None)
    palette._position_on_screen(anchor=anchor)

    # Above the text and to its right, so it does not sit over the writing.
    assert palette.x() > right - palette.width()
    assert palette.y() + palette.height() <= top


def test_dragging_publishes_an_offset_measured_from_the_text(app):
    palette = RewritePalette()
    anchor = (100, 200, 400, 260)
    palette._anchor = anchor
    published = []
    palette.badge_offset_changed.connect(
        lambda x, y: published.append((x, y))
    )
    screen = QApplication.primaryScreen()
    _left, top, right, _bottom = _anchor_in_screen_points(anchor, screen)

    palette.move(right + 12, top - 30)
    palette._remember_badge_offset()

    assert published == [(12, -30)]
    # And the same drop is not written twice.
    palette._remember_badge_offset()
    assert published == [(12, -30)]
