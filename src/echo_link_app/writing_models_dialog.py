from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from echo_link_core.writing_models import (
    LocalWritingModelManager,
    WritingModelBenchmark,
    WritingModelError,
    WritingModelImport,
    WritingModelInfo,
)


class _WritingModelBridge(QObject):
    refresh_ready = Signal(object)
    action_ready = Signal(object)


class WritingModelsDialog(QDialog):
    """Local-only writing model import, inspection, selection, and benchmark UI."""

    def __init__(
        self,
        configured_model: str,
        parent=None,
        *,
        manager: LocalWritingModelManager | None = None,
        auto_refresh: bool = True,
    ) -> None:
        super().__init__(parent)
        self.manager = manager or LocalWritingModelManager()
        self.configured_model = (configured_model or "gemma4:12b").strip()
        self.selected_model = self.configured_model
        self._preferred_selection = self.configured_model
        self.bridge = _WritingModelBridge(self)
        self.bridge.refresh_ready.connect(self._handle_refresh)
        self.bridge.action_ready.connect(self._handle_action)
        self.setWindowTitle("Local writing AI")
        self.setMinimumSize(760, 500)
        self.resize(900, 560)
        self._build_ui()
        if auto_refresh:
            self._refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)
        title = QLabel("Local writing AI")
        title.setObjectName("sectionTitle")
        root.addWidget(title)
        description = QLabel(
            "AI stays on this computer. Import accepts an existing local GGUF file. "
            "No download is performed."
        )
        description.setWordWrap(True)
        description.setAccessibleName("Local-only writing AI notice")
        root.addWidget(description)

        import_row = QHBoxLayout()
        self.model_name_edit = QLineEdit("echo-link-writing:latest")
        self.model_name_edit.hide()
        self.import_button = QPushButton("Import local GGUF")
        self.import_button.setAccessibleName("Import local writing AI")
        self.import_button.clicked.connect(self._import_gguf)
        import_row.addWidget(self.import_button)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self._refresh)
        import_row.addWidget(self.refresh_button)
        root.addLayout(import_row)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["AI", "Version", "Size", "Digest", "Family", "Quantization"]
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
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._sync_actions)
        root.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.benchmark_button = QPushButton("Run local speed check")
        self.benchmark_button.setEnabled(False)
        self.benchmark_button.clicked.connect(self._benchmark_selected)
        actions.addWidget(self.benchmark_button)
        self.remove_button = QPushButton("Remove selected")
        self.remove_button.setObjectName("dangerButton")
        self.remove_button.setEnabled(False)
        self.remove_button.clicked.connect(self._remove_selected)
        actions.addWidget(self.remove_button)
        actions.addStretch(1)
        self.use_button = QPushButton("Use selected")
        self.use_button.setObjectName("primaryButton")
        self.use_button.setEnabled(False)
        self.use_button.clicked.connect(self._use_selected)
        actions.addWidget(self.use_button)
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
        self.import_button.setEnabled(not busy)
        self.table.setEnabled(not busy)
        if busy:
            self.benchmark_button.setEnabled(False)
            self.remove_button.setEnabled(False)
            self.use_button.setEnabled(False)
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
            self.status.setText(payload[1])
            self._set_busy(False)
            return
        _kind, _version, models = payload
        self._populate_models(tuple(models))
        self.status.setText(f"{len(models)} local writing AI packs found.")
        self._set_busy(False)

    def _populate_models(self, models: tuple[WritingModelInfo, ...]) -> None:
        self.table.setRowCount(len(models))
        selected_row = -1
        for row, model in enumerate(models):
            name_item = QTableWidgetItem(f"Local AI {row + 1}")
            name_item.setData(Qt.ItemDataRole.UserRole, model.name)
            name_item.setToolTip(model.digest or "No digest reported")
            values = (
                name_item,
                QTableWidgetItem(model.version),
                QTableWidgetItem(_format_bytes(model.size_bytes)),
                QTableWidgetItem(_short_digest(model.digest)),
                QTableWidgetItem(model.family or model.format or "Unknown"),
                QTableWidgetItem(model.quantization or "Unknown"),
            )
            for column, item in enumerate(values):
                self.table.setItem(row, column, item)
            if _same_model(model.name, self._preferred_selection):
                selected_row = row
        if selected_row >= 0:
            self.table.selectRow(selected_row)
        else:
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
        self.use_button.setEnabled(ready)
        self.remove_button.setEnabled(ready and not _same_model(name, self.configured_model))

    def _import_gguf(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "Choose local writing AI",
            "",
            "GGUF file (*.gguf)",
        )
        if not path:
            return
        name = self.model_name_edit.text().strip()
        self._set_busy(True)
        self.status.setText("Hashing and importing local AI. This can take several minutes...")

        def work() -> None:
            try:
                result = self.manager.import_gguf(path, name=name)
                payload = ("imported", result)
            except (ValueError, WritingModelError) as exc:
                payload = ("error", str(exc))
            self.bridge.action_ready.emit(payload)

        threading.Thread(target=work, daemon=True, name="echo-link-writing-model-import").start()

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

    def _remove_selected(self) -> None:
        name = self._selected_name()
        if not name or _same_model(name, self.configured_model):
            return
        answer = QMessageBox.question(
            self,
            "Remove local writing AI",
            "Remove the selected AI pack from this computer?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer is not QMessageBox.StandardButton.Yes:
            return
        self._set_busy(True)
        self.status.setText("Removing the selected AI pack from this computer...")

        def work() -> None:
            try:
                self.manager.remove_model(name)
                payload = ("removed", name)
            except (ValueError, WritingModelError) as exc:
                payload = ("error", str(exc))
            self.bridge.action_ready.emit(payload)

        threading.Thread(target=work, daemon=True, name="echo-link-writing-model-remove").start()

    def _handle_action(self, payload) -> None:
        kind = payload[0]
        if kind == "error":
            self.status.setText(payload[1])
            self._set_busy(False)
            return
        if kind == "imported":
            result: WritingModelImport = payload[1]
            self._preferred_selection = result.model.name
            self.status.setText(
                f"Imported local AI. Source SHA256: {result.source_sha256}. "
                "Refreshing local AI details..."
            )
            self._set_busy(False)
            self._refresh()
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
        if kind == "removed":
            self.status.setText("Removed the selected AI pack. Refreshing...")
            self._set_busy(False)
            self._refresh()

    def _use_selected(self) -> None:
        name = self._selected_name()
        if not name:
            return
        self.selected_model = name
        self.accept()


def _same_model(left: str, right: str) -> bool:
    left_value = (left or "").casefold()
    right_value = (right or "").casefold()
    if left_value == right_value:
        return True
    if ":" in left_value and ":" in right_value:
        return False
    return left_value.split(":", 1)[0] == right_value.split(":", 1)[0]


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
