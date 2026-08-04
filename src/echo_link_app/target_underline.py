from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QApplication, QWidget


_RED = "#FF3B4F"
_LINE_HEIGHT = 3


class _UnderlineSegment(QWidget):
    def __init__(self) -> None:
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
        )
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet(f"background: {_RED}; border-radius: 1px;")


class TargetUnderline:
    """Small click-through red line windows for the active HUD text range."""

    def __init__(self) -> None:
        self._segments: list[_UnderlineSegment] = []

    def show_rectangles(self, rectangles: Sequence[tuple[int, int, int, int]]) -> None:
        safe = _safe_line_rectangles(rectangles)
        self._ensure_segments(len(safe))
        for segment, geometry in zip(self._segments, safe):
            segment.setGeometry(geometry)
            segment.show()
            segment.raise_()
        for segment in self._segments[len(safe) :]:
            segment.hide()

    def _ensure_segments(self, count: int) -> None:
        while len(self._segments) < count:
            self._segments.append(_UnderlineSegment())

    def clear(self) -> None:
        for segment in self._segments:
            segment.hide()

    def close(self) -> None:
        for segment in self._segments:
            segment.close()
        self._segments.clear()


def _safe_line_rectangles(
    rectangles: Sequence[tuple[int, int, int, int]],
) -> tuple[QRect, ...]:
    app = QApplication.instance()
    if app is None:
        return ()
    screens = tuple(app.screens())
    if not screens:
        return ()
    safe: list[QRect] = []
    for raw in rectangles[:100]:
        try:
            left, top, right, bottom = (int(value) for value in raw)
        except (TypeError, ValueError, OverflowError):
            continue
        if right <= left or bottom <= top:
            continue
        screen = _screen_for_physical_point(screens, (left + right) // 2, (top + bottom) // 2)
        if screen is None:
            continue
        geometry = screen.geometry()
        ratio = max(1.0, float(screen.devicePixelRatio()))
        physical_left = geometry.left()
        physical_top = geometry.top()
        logical_left = geometry.left() + round((left - physical_left) / ratio)
        logical_right = geometry.left() + round((right - physical_left) / ratio)
        logical_bottom = geometry.top() + round((bottom - physical_top) / ratio)
        line = QRect(
            logical_left,
            logical_bottom,
            max(1, logical_right - logical_left),
            _LINE_HEIGHT,
        ).intersected(geometry.adjusted(1, 1, -1, -1))
        if line.isValid() and 1 <= line.width() <= geometry.width():
            safe.append(line)
    return tuple(safe)


def _screen_for_physical_point(screens, x: int, y: int):
    for screen in screens:
        geometry = screen.geometry()
        ratio = max(1.0, float(screen.devicePixelRatio()))
        physical = QRect(
            geometry.left(),
            geometry.top(),
            round(geometry.width() * ratio),
            round(geometry.height() * ratio),
        )
        if physical.contains(QPoint(x, y)):
            return screen
    return None
