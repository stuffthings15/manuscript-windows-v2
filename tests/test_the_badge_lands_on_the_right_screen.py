"""The badge was converted for one screen and placed on another.

Two separate faults, both invisible on a single-monitor desk, which is why
neither was caught: every existing test builds its anchor as
`int(round(screen.left() * scale))` against `QApplication.primaryScreen()`, so
the screen origin is always zero and the two coordinate spaces coincide.

1. `_anchor_in_screen_points` divided a virtual-desktop coordinate by the
   device pixel ratio with no origin offset. On a second monitor whose logical
   origin is x=1920 at 150 percent, a UI Automation point at physical x=2500
   became 2500/1.5 = 1667 - a position on the PRIMARY monitor, most of a screen
   away from the text it was meant to sit beside.

2. `_position_on_screen` chose `screenAt(QCursor.pos())`. That is where the
   pointer is, which is normally the HUD the writer just clicked, not the
   document. So even the scale factor came from the wrong display.

`target_underline._safe_line_rectangles` has always re-offset from the screen
origin, and its underline landed correctly while the badge did not - so the
correct arithmetic was already in the codebase, in a sibling module, unused by
the code that needed it. The screen lookup is now shared rather than copied.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

from echo_link_app.rewrite_palette import _anchor_in_screen_points  # noqa: E402
from echo_link_app.target_underline import screen_for_physical_point  # noqa: E402


class _Rect:
    def __init__(self, left, top, width, height):
        self._left, self._top, self._width, self._height = left, top, width, height

    def left(self):
        return self._left

    def top(self):
        return self._top

    def width(self):
        return self._width

    def height(self):
        return self._height

    def contains(self, point):
        x, y = point
        return (
            self._left <= x < self._left + self._width
            and self._top <= y < self._top + self._height
        )


class _Screen:
    """A screen with a logical origin and a scale, which is all this needs."""

    def __init__(self, left, top, width, height, ratio):
        self._geometry = _Rect(left, top, width, height)
        self._ratio = ratio

    def geometry(self):
        return self._geometry

    def devicePixelRatio(self):
        return self._ratio


PRIMARY = _Screen(0, 0, 1920, 1080, 1.0)
SECOND_SCALED = _Screen(1920, 0, 1280, 800, 1.5)
# 1.25, not 1.0: at a scale of one both the correct formula and the broken
# one give the same answer, so a test at 1.0 cannot tell them apart.
LEFT_OF_PRIMARY = _Screen(-1920, 0, 1920, 1080, 1.25)


@pytest.mark.skipif(sys.platform != "win32", reason="the conversion is Windows only")
def test_a_point_on_a_scaled_second_monitor_stays_on_it():
    """The defect, in the arrangement that produces it."""
    # A caret 580 physical pixels into a screen that starts at logical 1920.
    left, top, right, bottom = _anchor_in_screen_points(
        (2500, 300, 2600, 340), SECOND_SCALED
    )
    assert left >= 1920, (
        f"the anchor converted to x={left}, which is on the primary monitor. "
        f"Dividing by the scale without re-offsetting from the screen origin "
        f"is what moved it there."
    )
    # 1920 + (2500-1920)/1.5 = 1920 + 387
    assert left == 1920 + round(580 / 1.5)
    assert right == 1920 + round(680 / 1.5)


@pytest.mark.skipif(sys.platform != "win32", reason="the conversion is Windows only")
def test_a_screen_left_of_the_primary_keeps_its_negative_origin():
    """A display positioned to the left gives the primary a positive origin
    and itself a negative one, which a bare division cannot represent."""
    left, _top, _right, _bottom = _anchor_in_screen_points(
        (-1000, 100, -900, 140), LEFT_OF_PRIMARY
    )
    assert left < 0, f"a point on the left-hand monitor converted to x={left}"
    # -1920 + 920/1.25 = -1184. The bare division gives -800, which is on the
    # primary monitor.
    assert left == -1920 + round(920 / 1.25)


@pytest.mark.skipif(sys.platform != "win32", reason="the conversion is Windows only")
def test_the_primary_screen_is_unchanged():
    """The single-monitor case every existing test covers must not move."""
    assert _anchor_in_screen_points((100, 200, 300, 240), PRIMARY) == (100, 200, 300, 240)


def test_the_screen_is_found_from_the_anchor_not_the_cursor():
    """A point in the second monitor's physical space resolves to it."""
    screens = (PRIMARY, SECOND_SCALED)
    found = screen_for_physical_point(screens, 2500, 320)
    assert found is SECOND_SCALED, (
        "the anchor resolved to the wrong screen, so its scale factor and its "
        "placement both come from a display the document is not on"
    )


def test_a_point_on_the_primary_still_resolves_to_the_primary():
    screens = (PRIMARY, SECOND_SCALED)
    assert screen_for_physical_point(screens, 400, 500) is PRIMARY


def test_a_point_on_no_screen_resolves_to_nothing():
    """Placement falls back to the cursor rather than guessing."""
    screens = (PRIMARY,)
    assert screen_for_physical_point(screens, 99_999, 99_999) is None


def test_the_placement_asks_the_anchor_for_its_screen():
    """The live check: _position_on_screen consults the anchor.

    The conversion above can be exercised directly; the choice of screen sits
    inside a Qt dialog, so the property is asserted against the parsed source.
    """
    import ast

    tree = ast.parse(
        (ROOT / "src" / "echo_link_app" / "rewrite_palette.py").read_text(
            encoding="utf-8"
        )
    )
    placement = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_position_on_screen"
    )
    called = {
        node.func.id
        for node in ast.walk(placement)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "screen_for_physical_point" in called, (
        "_position_on_screen does not locate the anchor's screen, so it uses "
        "whichever display the mouse is on - normally the HUD's, not the "
        "document's."
    )
