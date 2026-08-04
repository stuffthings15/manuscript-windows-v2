from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget

from echo_link_app.theme import COLORS
from echo_link_app.accessibility import set_accessible
from echo_link_core.models import SessionState


class EchoBar(QWidget):
    def __init__(self, *, x: int | None = None, y: int | None = None, position_changed=None):
        super().__init__(None)
        self._saved_position = QPoint(x, y) if x is not None and y is not None else None
        self._position_changed = position_changed
        self._drag_offset: QPoint | None = None
        self.setObjectName("echoBar")
        set_accessible(
            self,
            "Manuscript Bar dictation status",
            "A local microphone status display that never takes keyboard focus.",
        )
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.resize(460, self.sizeHint().height())
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        panel = QFrame()
        panel.setObjectName("echoBarPanel")
        panel.setStyleSheet(
            "QFrame#echoBarPanel { "
            f"background: {COLORS['window']}; border: 2px solid {COLORS['border']}; "
            "border-radius: 14px; }"
        )
        content = QVBoxLayout(panel)
        content.setContentsMargins(18, 13, 18, 13)
        content.setSpacing(8)
        row = QHBoxLayout()
        self.dot = QFrame()
        self.dot.setFixedSize(14, 14)
        row.addWidget(self.dot)
        self.status = QLabel("Ready")
        self.status.setWordWrap(True)
        set_accessible(self.status, "Manuscript Bar status", "Ready")
        self.status.setStyleSheet(
            f"color: {COLORS['text']}; font-size: 15px; font-weight: 700;"
        )
        row.addWidget(self.status, 1)
        self.privacy = QLabel("LOCAL ONLY")
        set_accessible(self.privacy, "Privacy status", "Local only")
        self.privacy.setStyleSheet(
            f"color: {COLORS['accent_text']}; font-size: 10px; font-weight: 700;"
        )
        row.addWidget(self.privacy)
        content.addLayout(row)
        self.hint = QLabel("Microphone ready")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color: {COLORS['muted']}; font-size: 11px;")
        set_accessible(self.hint, "Manuscript Bar action hint", "Microphone ready")
        content.addWidget(self.hint)
        self.level = QProgressBar()
        self.level.setRange(0, 100)
        self.level.setValue(0)
        self.level.setTextVisible(False)
        self.level.setFixedHeight(9)
        set_accessible(
            self.level,
            "Manuscript Bar microphone level",
            "Live microphone level from 0 to 100.",
        )
        content.addWidget(self.level)
        root.addWidget(panel)

    def show_state(self, state: SessionState, message: str) -> None:
        color = COLORS["green"]
        label = message
        hint = "Local microphone ready"
        if state is SessionState.LISTENING:
            color = COLORS["red"]
            if message.casefold().startswith("silence detected"):
                label = "Silence detected - finishing locally..."
                hint = "Keep this window focused; text is processing offline"
            elif "hands-free" in message.casefold():
                label = "Hands-free listening - Esc cancels"
                hint = "Speak normally. Press Esc to discard the recording"
            else:
                label = "Listening - release hotkey to finish"
                hint = "Speak normally. The active text box stays focused"
        elif state in {SessionState.TRANSCRIBING, SessionState.REFINING}:
            color = COLORS["amber"]
            label = "Processing locally..."
            hint = "Offline speech and writing AI are working"
        elif state is SessionState.INSERTED:
            label = "Text inserted"
            hint = "Returned to the active text box"
        elif state is SessionState.ERROR:
            color = COLORS["red"]
            label = message or "Dictation failed"
            hint = "Open Manuscript for the error and recovery options"
        elif state is SessionState.COMPLETE:
            label = "Done"
            hint = "Ready for the next dictation"
        self.status.setText(label)
        self.status.setAccessibleDescription(label)
        self.hint.setText(hint)
        self.hint.setAccessibleDescription(hint)
        self.dot.setStyleSheet(f"background: {color}; border-radius: 7px;")
        self._position()
        self.show()
        self.raise_()
        if state in {SessionState.COMPLETE, SessionState.READY}:
            QTimer.singleShot(1200, self.hide)
        elif state is SessionState.ERROR:
            QTimer.singleShot(3500, self.hide)

    def set_level(self, value: float) -> None:
        self.level.setValue(round(max(0.0, min(1.0, float(value))) * 100))

    def _position(self) -> None:
        primary = QGuiApplication.primaryScreen()
        screen = QGuiApplication.screenAt(primary.geometry().center()) if primary else None
        if screen is None:
            screen = primary
        if screen is None:
            return
        area = screen.availableGeometry()
        target_width = max(280, min(460, area.width() - 16))
        self.setFixedWidth(target_width)
        self.adjustSize()
        if self._saved_position is None:
            point = QPoint(area.x() + (area.width() - self.width()) // 2, area.y() + 28)
        else:
            point = self._clamp(self._saved_position)
            self._saved_position = point
        self.move(point)

    def set_saved_position(self, x: int | None, y: int | None) -> None:
        self._saved_position = QPoint(x, y) if x is not None and y is not None else None
        self._position()

    def move_to(self, point: QPoint, *, save: bool = False) -> QPoint:
        clamped = self._clamp(point)
        self.move(clamped)
        if save:
            self._saved_position = clamped
            if self._position_changed is not None:
                self._position_changed(clamped.x(), clamped.y())
        return clamped

    def _clamp(self, point: QPoint) -> QPoint:
        screen = QGuiApplication.screenAt(point)
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return point
        area = screen.availableGeometry()
        max_x = max(area.x(), area.right() - self.width() + 1)
        max_y = max(area.y(), area.bottom() - self.height() + 1)
        return QPoint(
            min(max(point.x(), area.x()), max_x),
            min(max(point.y(), area.y()), max_y),
        )

    def mousePressEvent(self, event) -> None:
        if event.button() is Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move_to(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() is Qt.MouseButton.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            self.move_to(self.pos(), save=True)
            event.accept()
            return
        super().mouseReleaseEvent(event)
