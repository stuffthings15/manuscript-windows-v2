"""The badge sits to the right of the text it was captured from.

It used to anchor to the left. Moving it right is only half the job: the
anchor is often a whole text box whose right edge is already at the edge of
the window, and the first version fell back to the left in exactly that case,
which is most of them.
"""

import pytest
from PySide6.QtWidgets import QApplication

from echo_link_app.rewrite_palette import RewritePalette


@pytest.fixture
def app():
    application = QApplication.instance() or QApplication([])
    yield application
    application.processEvents()


def _placed(app, anchor) -> tuple[int, int]:
    palette = RewritePalette()
    palette.show_capture("Please review this sentence.", automatic=True, anchor=anchor)
    app.processEvents()
    position = (palette.x(), palette.width())
    palette.close()
    palette.deleteLater()
    app.processEvents()
    return position


def test_a_narrow_selection_puts_the_badge_just_past_its_right_edge(app):
    screen = QApplication.primaryScreen().availableGeometry()
    scale = QApplication.primaryScreen().devicePixelRatio()
    right = int(round((screen.left() + 200) * scale))
    left = int(round((screen.left() + 100) * scale))
    x, _width = _placed(app, (left, 200, right, 240))

    assert x > screen.left() + 100


def test_a_full_width_text_box_keeps_the_badge_on_the_right(app):
    # The regression: with the anchor's right edge at the screen edge there is
    # no room outside it, and the badge used to jump to the left of the box.
    screen = QApplication.primaryScreen().availableGeometry()
    scale = QApplication.primaryScreen().devicePixelRatio()
    left = int(round(screen.left() * scale))
    right = int(round(screen.right() * scale))
    x, width = _placed(app, (left, 200, right, 400))

    assert x + width > screen.left() + screen.width() // 2
