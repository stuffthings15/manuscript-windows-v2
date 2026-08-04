from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from echo_link_core.learning import LearningProposal


class LearningProposalDialog(QDialog):
    def __init__(self, proposals: tuple[LearningProposal, ...], parent=None):
        super().__init__(parent)
        self.proposals = proposals
        self.setWindowTitle("Approve learned corrections")
        self.setMinimumSize(620, 320)
        layout = QVBoxLayout(self)
        title = QLabel("Save small corrections for next time?")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        note = QLabel(
            "Nothing is saved unless you check it below. Manuscript stores only the short From and To "
            "phrases on this computer - never audio or the full dictation."
        )
        note.setWordWrap(True)
        note.setObjectName("mutedLabel")
        layout.addWidget(note)

        self.table = QTableWidget(len(proposals), 3)
        self.table.setHorizontalHeaderLabels(["Save", "From", "To"])
        self.table.setAccessibleName("Learned correction proposals")
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for row, proposal in enumerate(proposals):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckState.Unchecked)
            self.table.setItem(row, 0, check)
            self.table.setItem(row, 1, QTableWidgetItem(proposal.source))
            self.table.setItem(row, 2, QTableWidgetItem(proposal.replacement))
        layout.addWidget(self.table, 1)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.save_button = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setText("Save selected")
        self.save_button.setEnabled(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Not now")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.table.itemChanged.connect(self._sync_save_button)
        layout.addWidget(self.buttons)

    def _sync_save_button(self, _item=None) -> None:
        self.save_button.setEnabled(bool(self.selected_proposals()))

    def selected_proposals(self) -> tuple[LearningProposal, ...]:
        return tuple(
            proposal
            for row, proposal in enumerate(self.proposals)
            if self.table.item(row, 0).checkState() == Qt.CheckState.Checked
        )


class LearningManagerDialog(QDialog):
    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Learned corrections")
        self.setMinimumSize(680, 420)
        layout = QVBoxLayout(self)
        title = QLabel("Approved learned corrections")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        note = QLabel(
            "These short phrase pairs stay on this computer. Disable, delete, or reset them at any time."
        )
        note.setWordWrap(True)
        note.setObjectName("mutedLabel")
        layout.addWidget(note)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Status", "From", "To"])
        self.table.setAccessibleName("Saved learned corrections")
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.toggle_button = QPushButton("Enable or disable")
        self.toggle_button.clicked.connect(self._toggle_selected)
        actions.addWidget(self.toggle_button)
        self.delete_button = QPushButton("Delete selected")
        self.delete_button.setObjectName("dangerButton")
        self.delete_button.clicked.connect(self._delete_selected)
        actions.addWidget(self.delete_button)
        self.clear_button = QPushButton("Reset all")
        self.clear_button.setObjectName("dangerButton")
        self.clear_button.clicked.connect(self._clear_all)
        actions.addWidget(self.clear_button)
        actions.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        actions.addWidget(close_button)
        layout.addLayout(actions)
        self._refresh()

    def _refresh(self) -> None:
        rows = self.store.list_learned_corrections()
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            status = QTableWidgetItem("On" if row["enabled"] else "Off")
            status.setData(Qt.ItemDataRole.UserRole, int(row["id"]))
            self.table.setItem(row_index, 0, status)
            self.table.setItem(row_index, 1, QTableWidgetItem(str(row["source_text"])))
            self.table.setItem(row_index, 2, QTableWidgetItem(str(row["replacement_text"])))
        enabled = bool(rows)
        self.toggle_button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)
        self.clear_button.setEnabled(enabled)

    def _selected(self) -> tuple[int, bool] | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        return int(item.data(Qt.ItemDataRole.UserRole)), item.text() == "On"

    def _toggle_selected(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        item_id, enabled = selected
        self.store.set_learned_correction_enabled(item_id, not enabled)
        self._refresh()

    def _delete_selected(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        self.store.delete_learned_correction(selected[0])
        self._refresh()

    def _clear_all(self) -> None:
        count = len(self.store.list_learned_corrections())
        if not count:
            return
        answer = QMessageBox.question(
            self,
            "Reset learned corrections",
            f"Delete all {count} learned correction(s)? Custom vocabulary is not affected.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer is QMessageBox.StandardButton.Yes:
            self.store.clear_learned_corrections()
            self._refresh()
