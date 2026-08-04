from __future__ import annotations

import logging

from PySide6.QtCore import QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from echo_link_core.subscription import (
    EntitlementClient,
    EntitlementResult,
    SubscriptionError,
    SubscriptionState,
    SubscriptionStateStore,
    subscription_origin,
)


class SubscriptionDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Activate Manuscript")
        self.setMinimumWidth(500)
        self.setModal(True)
        self._state_store = SubscriptionStateStore()
        self._origin = ""
        self._client: EntitlementClient | None = None

        layout = QVBoxLayout(self)
        title = QLabel("An active subscription is required")
        title.setObjectName("dialogTitle")
        title.setAccessibleName("Subscription activation")
        description = QLabel(
            "Subscribe on the Manuscript website, then paste the license key shown after checkout. "
            "Only subscription and installation identifiers are sent for verification."
        )
        description.setWordWrap(True)
        self.license_edit = QLineEdit()
        self.license_edit.setPlaceholderText("MSCR-XXXXXXXX-XXXXXXXX-XXXXXXXX-XXXXXXXX")
        self.license_edit.setAccessibleName("Manuscript license key")
        self.license_edit.returnPressed.connect(self._activate)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setAccessibleName("Activation status")

        buttons = QHBoxLayout()
        self.subscribe_button = QPushButton("Open subscription page")
        self.subscribe_button.clicked.connect(self._open_subscription_page)
        self.activate_button = QPushButton("Activate")
        self.activate_button.setDefault(True)
        self.activate_button.clicked.connect(self._activate)
        buttons.addWidget(self.subscribe_button)
        buttons.addStretch(1)
        buttons.addWidget(self.activate_button)

        layout.addWidget(title)
        layout.addWidget(description)
        layout.addSpacing(10)
        layout.addWidget(QLabel("License key"))
        layout.addWidget(self.license_edit)
        layout.addWidget(self.status_label)
        layout.addLayout(buttons)

        try:
            self._origin = subscription_origin()
            self._client = EntitlementClient(self._origin)
            self._check_saved_state()
        except SubscriptionError as exc:
            self._set_error(str(exc))
            self.activate_button.setEnabled(False)
            self.subscribe_button.setEnabled(False)

    @property
    def active_state(self) -> SubscriptionState | None:
        try:
            return self._state_store.load()
        except SubscriptionError:
            return None

    def _check_saved_state(self) -> None:
        try:
            state = self._state_store.load()
            if state is None:
                self.license_edit.setFocus()
                return
            self.license_edit.setText(state.license_key)
            result = self._validate(state)
            if result.active:
                self.accept()
            else:
                self._set_error(_inactive_message(result))
        except SubscriptionError as exc:
            self._set_error(str(exc))

    def _activate(self) -> None:
        if self._client is None:
            return
        self.activate_button.setEnabled(False)
        self.status_label.setText("Checking subscription…")
        try:
            previous = self._state_store.load()
            state = self._state_store.save(
                self.license_edit.text(),
                device_secret=previous.device_secret if previous else None,
            )
            result = self._validate(state)
            if result.active:
                self.status_label.setText("Subscription active.")
                self.accept()
            else:
                self._state_store.clear()
                self._set_error(_inactive_message(result))
        except SubscriptionError as exc:
            self._set_error(str(exc))
        finally:
            self.activate_button.setEnabled(True)

    def _validate(self, state: SubscriptionState) -> EntitlementResult:
        if self._client is None:
            raise SubscriptionError("Manuscript subscription service is not configured.")
        return self._client.validate(state.license_key, state.device_hash)

    def _open_subscription_page(self) -> None:
        if self._origin:
            QDesktopServices.openUrl(QUrl(self._origin))

    def _set_error(self, message: str) -> None:
        self.status_label.setText(message)
        self.status_label.setStyleSheet("color: #ff8a8a;")


class EntitlementCheckThread(QThread):
    completed = Signal(bool, str)

    def run(self) -> None:
        try:
            state = SubscriptionStateStore().load()
            if state is None:
                self.completed.emit(False, "Subscription activation is missing.")
                return
            client = EntitlementClient(subscription_origin())
            result = client.validate(state.license_key, state.device_hash)
            self.completed.emit(result.active, "" if result.active else _inactive_message(result))
        except SubscriptionError as exc:
            logging.getLogger(__name__).warning("Subscription recheck failed: %s", exc)
            self.completed.emit(False, str(exc))


def _inactive_message(result: EntitlementResult) -> str:
    if result.reason == "device_limit":
        return "This subscription has reached its installation limit. Contact support to reset an installation."
    if result.reason == "subscription_inactive":
        return "This subscription is not active. Update billing or start a new subscription."
    return "The license key could not be verified for an active subscription."


__all__ = ["EntitlementCheckThread", "SubscriptionDialog"]
