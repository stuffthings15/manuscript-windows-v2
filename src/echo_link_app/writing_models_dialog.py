from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from echo_link_core.writing_models import (
    LocalWritingModelManager,
    WritingModelBenchmark,
    WritingModelError,
    WritingModelInfo,
)


class _WritingModelBridge(QObject):
    refresh_ready = Signal(object)
    action_ready = Signal(object)


class WritingModelsDialog(QDialog):
    """Read-only diagnostics for the automatically selected local writing AI."""

    def __init__(
        self,
        configured_model: str,
        parent=None,
        *,
        manager: LocalWritingModelManager | None = None,
        auto_refresh: bool = True,
    ) -> None:
        super().__init__(parent)
        del configured_model
        self.manager = manager or LocalWritingModelManager()
        self.configured_model = ""
        self.selected_model = ""
        self.bridge = _WritingModelBridge(self)
        self.bridge.refresh_ready.connect(self._handle_refresh)
        self.bridge.action_ready.connect(self._handle_action)
        self.setWindowTitle("Local AI diagnostics")
        self.setMinimumSize(760, 500)
        self.resize(900, 560)
        self._build_ui()
        if auto_refresh:
            self._refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)
        title = QLabel("Local AI diagnostics")
        title.setObjectName("sectionTitle")
        root.addWidget(title)
        description = QLabel(
            "Manuscript chooses an approved AI automatically after checking this "
            "computer. This page only shows local diagnostics."
        )
        description.setWordWrap(True)
        description.setAccessibleName("Local-only writing AI notice")
        root.addWidget(description)

        import_row = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self._refresh)
        import_row.addWidget(self.refresh_button)
        root.addLayout(import_row)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["AI", "Version", "Size", "Digest", "Quantization"]
        )
        self.table.setAccessibleName("Installed local writing AI")
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._sync_actions)
        root.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.benchmark_button = QPushButton("Run local speed check")
        self.benchmark_button.setEnabled(False)
        self.benchmark_button.clicked.connect(self._benchmark_selected)
        actions.addWidget(self.benchmark_button)
        actions.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.reject)
        actions.addWidget(close_button)
        root.addLayout(actions)

        self.status = QLabel("Checking local writing AI...")
        self.status.setWordWrap(True)
        self.status.setAccessibleName("Writing AI status")
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.status)

    def _set_busy(self, busy: bool) -> None:
        self.refresh_button.setEnabled(not busy)
        self.table.setEnabled(not busy)
        if busy:
            self.benchmark_button.setEnabled(False)
        else:
            self._sync_actions()

    def _refresh(self) -> None:
        if not self.refresh_button.isEnabled():
            return
        self._set_busy(True)
        self.status.setText("Checking installed local writing AI...")

        def work() -> None:
            try:
                payload = ("models", self.manager.server_version(), self.manager.list_models())
            except WritingModelError as exc:
                payload = ("error", str(exc))
            self.bridge.refresh_ready.emit(payload)

        threading.Thread(target=work, daemon=True, name="echo-link-writing-model-list").start()

    def _handle_refresh(self, payload) -> None:
        if payload[0] == "error":
            self.table.setRowCount(0)
            self.status.setText("Local AI diagnostics are unavailable. Retry shortly.")
            self._set_busy(False)
            return
        _kind, _version, models = payload
        self._populate_models(tuple(models))
        self.status.setText(f"{len(models)} local writing AI packs found.")
        self._set_busy(False)

    def _populate_models(self, models: tuple[WritingModelInfo, ...]) -> None:
        self.table.setRowCount(len(models))
        for row, model in enumerate(models):
            name_item = QTableWidgetItem(f"Local AI {row + 1}")
            name_item.setData(Qt.ItemDataRole.UserRole, model.name)
            name_item.setToolTip(model.digest or "No digest reported")
            values = (
                name_item,
                QTableWidgetItem(model.version),
                QTableWidgetItem(_format_bytes(model.size_bytes)),
                QTableWidgetItem(_short_digest(model.digest)),
                QTableWidgetItem(model.quantization or "Unknown"),
            )
            for column, item in enumerate(values):
                self.table.setItem(row, column, item)
        self.table.clearSelection()
        self._sync_actions()

    def _selected_name(self) -> str:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item is not None else ""

    def _sync_actions(self) -> None:
        name = self._selected_name()
        ready = bool(name) and self.table.isEnabled()
        self.benchmark_button.setEnabled(ready)

    def _benchmark_selected(self) -> None:
        name = self._selected_name()
        if not name:
            return
        self._set_busy(True)
        self.status.setText("Running a short local-only AI speed check...")

        def work() -> None:
            try:
                payload = ("benchmark", self.manager.benchmark(name))
            except (ValueError, WritingModelError) as exc:
                payload = ("error", str(exc))
            self.bridge.action_ready.emit(payload)

        threading.Thread(target=work, daemon=True, name="echo-link-writing-model-benchmark").start()

    def _handle_action(self, payload) -> None:
        kind = payload[0]
        if kind == "error":
            self.status.setText("The local AI speed check could not finish. Retry shortly.")
            self._set_busy(False)
            return
        if kind == "benchmark":
            result: WritingModelBenchmark = payload[1]
            verification = "output check passed" if result.response_verified else "output check differed"
            self.status.setText(
                f"AI speed: {result.tokens_per_second:.1f} tokens/second, "
                f"load {result.load_seconds:.2f}s, total {result.elapsed_seconds:.2f}s; {verification}."
            )
            self._set_busy(False)
            return


def _short_digest(value: str) -> str:
    digest = (value or "").removeprefix("sha256:")
    return digest[:12] if digest else "Unknown"


def _format_bytes(value: int) -> str:
    amount = float(max(0, int(value)))
    for suffix in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024.0 or suffix == "TB":
            return f"{amount:.1f} {suffix}" if suffix != "B" else f"{int(amount)} B"
        amount /= 1024.0
    return f"{int(value)} B"
