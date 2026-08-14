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


def test_the_badge_sits_beside_the_text_inside_a_full_width_box(app):
    # "On the right" has to mean beside the writing. A previous fix parked the
    # badge at the far right of the display, which is the correct side and the
    # wrong place, so this anchor stops short of the screen edge: flush-right
    # placement lands past the anchor, and beside-the-text lands inside it.
    screen = QApplication.primaryScreen().availableGeometry()
    scale = QApplication.primaryScreen().devicePixelRatio()
    edge = screen.right() - 40
    left = int(round(screen.left() * scale))
    right = int(round(edge * scale))
    x, width = _placed(app, (left, 200, right, 400))

    assert x + width <= edge + 1


def test_the_badge_can_be_dragged_and_the_drag_does_not_open_the_card(app):
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    palette = RewritePalette()
    palette.show_capture("Please review this sentence.", automatic=True)
    app.processEvents()
    start = palette.pos()

    def send(kind, global_point, buttons):
        event = QMouseEvent(
            kind,
            QPointF(4.0, 4.0),
            QPointF(float(global_point.x()), float(global_point.y())),
            Qt.MouseButton.LeftButton,
            buttons,
            Qt.KeyboardModifier.NoModifier,
        )
        palette.eventFilter(palette.badge_button, event)

    origin = palette.mapToGlobal(QPoint(4, 4))
    send(QEvent.Type.MouseButtonPress, origin, Qt.MouseButton.LeftButton)
    send(
        QEvent.Type.MouseMove,
        origin + QPoint(60, 40),
        Qt.MouseButton.LeftButton,
    )
    send(QEvent.Type.MouseButtonRelease, origin + QPoint(60, 40), Qt.MouseButton.NoButton)
    app.processEvents()

    assert palette.pos() != start
    # The release that ends a drag must not also count as a click.
    palette._open_from_badge()
    assert palette._view == "badge"

    palette.close()
    palette.deleteLater()
    app.processEvents()


def test_a_click_without_movement_still_opens_the_card(app):
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    palette = RewritePalette()
    palette.show_capture("Please review this sentence.", automatic=True)
    app.processEvents()
    start = palette.pos()

    origin = palette.mapToGlobal(QPoint(4, 4))
    for kind, buttons in (
        (QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton),
        (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton),
    ):
        palette.eventFilter(
            palette.badge_button,
            QMouseEvent(
                kind,
                QPointF(4.0, 4.0),
                QPointF(float(origin.x()), float(origin.y())),
                Qt.MouseButton.LeftButton,
                buttons,
                Qt.KeyboardModifier.NoModifier,
            ),
        )
    app.processEvents()

    assert palette.pos() == start
    palette._open_from_badge()
    assert palette._view == "card"

    palette.close()
    palette.deleteLater()
    app.processEvents()
