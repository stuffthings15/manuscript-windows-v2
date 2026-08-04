from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent
from PySide6.QtWidgets import QFormLayout, QLabel, QWidget


def set_accessible(widget: QWidget, name: str, description: str = "") -> None:
    """Give a widget stable text for assistive technology."""
    widget.setAccessibleName(name.strip())
    if description:
        widget.setAccessibleDescription(description.strip())


def announce(widget: QWidget, message: str, *, assertive: bool = False) -> None:
    """Send a Qt accessibility announcement without moving keyboard focus."""
    text = message.strip()
    if not text:
        return
    event = QAccessibleAnnouncementEvent(widget, text)
    event.setPoliteness(
        QAccessible.AnnouncementPoliteness.Assertive
        if assertive
        else QAccessible.AnnouncementPoliteness.Polite
    )
    _send_accessibility_event(event)


def update_status_label(
    label: QLabel,
    message: str,
    *,
    assertive: bool = False,
    announce_change: bool = True,
) -> None:
    """Update visible status and its screen-reader equivalent together."""
    changed = label.text() != message
    label.setText(message)
    label.setAccessibleDescription(message)
    if changed and announce_change:
        announce(label, message, assertive=assertive)


def bind_form_labels(form: QFormLayout) -> None:
    """Associate form labels with their first keyboard-focusable field."""
    for row in range(form.rowCount()):
        label_item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
        field_item = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
        if label_item is None or field_item is None:
            continue
        label = label_item.widget()
        if not isinstance(label, QLabel):
            continue
        name = label.text().replace("&", "").strip().rstrip(":")
        if not name:
            continue
        field = _first_focusable_widget(field_item)
        if field is None:
            continue
        label.setBuddy(field)
        if not field.accessibleName().strip():
            field.setAccessibleName(name)


def _first_focusable_widget(item) -> QWidget | None:
    widget = item.widget()
    if widget is not None and widget.focusPolicy() != Qt.FocusPolicy.NoFocus:
        return widget
    layout = item.layout()
    if layout is None:
        return None
    for index in range(layout.count()):
        found = _first_focusable_widget(layout.itemAt(index))
        if found is not None:
            return found
    return None


def _send_accessibility_event(event: QAccessibleAnnouncementEvent) -> None:
    QAccessible.updateAccessibility(event)
