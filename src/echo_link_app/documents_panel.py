from __future__ import annotations

import re
import threading
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from echo_link_app.accessibility import set_accessible, update_status_label
from echo_link_app.ai_status import user_ai_status
from echo_link_app.theme import COLORS
from echo_link_core.document_analytics import DocumentAnalytics, analyze_document
from echo_link_core.documents import DocumentWorkspace, LocalDocument
from echo_link_core.local_paths import is_network_path
from echo_link_core.local_transport import validate_ollama_endpoint
from echo_link_core.selected_text import COMMANDS, PROFILES, LocalWritingAssistant


_ALLOWED_IMPORT_SUFFIXES = frozenset({".txt", ".md"})
_MAX_IMPORT_BYTES = 5 * 1024 * 1024


class DocumentEditorPanel(QWidget):
    """Local document editor with explicit, immutable save revisions."""

    assistant_result_ready = Signal(object)
    assistant_stage_requested = Signal(str)

    def __init__(
        self,
        workspace: DocumentWorkspace,
        parent: QWidget | None = None,
        *,
        writing_assistant: LocalWritingAssistant | None = None,
        writing_model_provider: Callable[[], str] | None = None,
        protected_terms_provider: Callable[[], Sequence[str]] | None = None,
        writing_timeout_provider: Callable[[], float] | None = None,
    ):
        super().__init__(parent)
        self.workspace = workspace
        self.writing_assistant = writing_assistant or LocalWritingAssistant()
        # A document assistant may run only with the model accepted by the
        # shared hardware policy and runtime probe. Never invent a fallback
        # model when this panel is used on its own.
        self._writing_model_provider = writing_model_provider or (lambda: "")
        self._protected_terms_provider = protected_terms_provider or (lambda: ())
        self._writing_timeout_provider = writing_timeout_provider or (lambda: 30.0)
        self.current_document_id: int | None = None
        self.loaded_revision: int | None = None
        self._dirty = False
        self._loading = False
        self._assistant_busy = False
        self._assistant_request_id = 0
        self._assistant_source_text = ""
        self._assistant_suppress_draft_change = False
        self.analytics_last_report: DocumentAnalytics | None = None
        self._build_ui()
        self._configure_accessibility()
        self.assistant_result_ready.connect(self._handle_assistant_result)
        self.refresh_documents()

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("Local documents")
        title.setObjectName("sectionTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.new_button = QPushButton("New")
        self.new_button.clicked.connect(self._new_document)
        header.addWidget(self.new_button)
        self.import_button = QPushButton("Import text")
        self.import_button.setToolTip("Import one local .txt or .md file into the workspace")
        self.import_button.clicked.connect(self._import_document)
        header.addWidget(self.import_button)
        self.export_button = QPushButton("Export")
        self.export_button.clicked.connect(self._export_document)
        header.addWidget(self.export_button)
        layout.addLayout(header)

        privacy = QLabel(
            "Documents and revisions stay in the local Manuscript database. "
            "Import and export happen only when you choose a file."
        )
        privacy.setObjectName("mutedLabel")
        privacy.setWordWrap(True)
        layout.addWidget(privacy)

        picker = QHBoxLayout()
        picker.addWidget(QLabel("Document"))
        self.document_combo = QComboBox()
        self.document_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.document_combo.setMinimumContentsLength(18)
        self.document_combo.currentIndexChanged.connect(self._document_selected)
        picker.addWidget(self.document_combo, 1)
        self.save_button = QPushButton("Save revision")
        self.save_button.clicked.connect(self.save_document)
        picker.addWidget(self.save_button)
        layout.addLayout(picker)

        self.status = QLabel("No document selected.")
        self.status.setObjectName("mutedLabel")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.view_tabs = QTabWidget()
        self.view_tabs.setDocumentMode(True)
        self.view_tabs.addTab(self._build_editor_page(), "Editor")
        self.view_tabs.addTab(self._build_revisions_page(), "Revisions")
        self.view_tabs.addTab(self._build_analytics_page(), "Analytics")
        self.view_tabs.addTab(self._build_assistant_page(), "Assistant")
        layout.addWidget(self.view_tabs, 1)
        self._set_document_controls(False)

    def _build_editor_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(QLabel("Title"))
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Document title")
        self.title_edit.textEdited.connect(self._mark_dirty)
        layout.addWidget(self.title_edit)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("Write here. Save creates a new immutable revision.")
        self.editor.textChanged.connect(self._mark_dirty)
        self.editor.textChanged.connect(self._assistant_draft_changed)
        layout.addWidget(self.editor, 1)
        return page

    def _build_revisions_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(8)
        self.revision_table = QTableWidget(0, 3)
        self.revision_table.setHorizontalHeaderLabels(["Revision", "Saved", "Label"])
        self.revision_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.revision_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.revision_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.revision_table.verticalHeader().setVisible(False)
        revision_header = self.revision_table.horizontalHeader()
        revision_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        revision_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        revision_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.revision_table.itemSelectionChanged.connect(self._preview_revision)
        layout.addWidget(self.revision_table, 1)
        self.revision_preview = QPlainTextEdit()
        self.revision_preview.setReadOnly(True)
        self.revision_preview.setPlaceholderText("Select a revision to preview it.")
        self.revision_preview.setMaximumHeight(140)
        layout.addWidget(self.revision_preview)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.restore_button = QPushButton("Restore selected")
        self.restore_button.setEnabled(False)
        self.restore_button.clicked.connect(self._restore_revision)
        actions.addWidget(self.restore_button)
        layout.addLayout(actions)
        return page

    def _build_analytics_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 10, 0, 0)
        root.setSpacing(8)

        action_row = QHBoxLayout()
        hint = QLabel("Analyze the current draft on this device. No AI or network is used.")
        hint.setObjectName("mutedLabel")
        hint.setWordWrap(True)
        action_row.addWidget(hint, 1)
        self.analytics_button = QPushButton("Analyze document")
        self.analytics_button.clicked.connect(self._analyze_clicked)
        action_row.addWidget(self.analytics_button)
        root.addLayout(action_row)

        self.analytics_scroll = QScrollArea()
        self.analytics_scroll.setWidgetResizable(True)
        self.analytics_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 4, 0, 4)
        content_layout.setSpacing(10)

        score_card = QFrame()
        score_card.setObjectName("entryBand")
        score_layout = QVBoxLayout(score_card)
        score_layout.setContentsMargins(14, 12, 14, 12)
        score_title = QLabel("Readability score")
        score_title.setObjectName("mutedLabel")
        score_layout.addWidget(score_title)
        score_row = QHBoxLayout()
        self.analytics_score = QLabel("-- / 100")
        self.analytics_score.setStyleSheet(
            f"font-size: 24pt; font-weight: 700; color: {COLORS['accent']};"
        )
        score_row.addWidget(self.analytics_score)
        self.analytics_score_label = QLabel("Not analyzed")
        self.analytics_score_label.setObjectName("sectionTitle")
        score_row.addWidget(self.analytics_score_label)
        score_row.addStretch(1)
        score_layout.addLayout(score_row)
        self.analytics_score_bar = QProgressBar()
        self.analytics_score_bar.setRange(0, 100)
        self.analytics_score_bar.setValue(0)
        self.analytics_score_bar.setTextVisible(False)
        score_layout.addWidget(self.analytics_score_bar)
        content_layout.addWidget(score_card)

        metrics_title = QLabel("Document metrics")
        metrics_title.setObjectName("sectionTitle")
        content_layout.addWidget(metrics_title)
        metrics = QGridLayout()
        metrics.setHorizontalSpacing(10)
        metrics.setVerticalSpacing(10)
        self.analytics_metrics: dict[str, QLabel] = {}
        metric_specs = (
            ("Words", "words"),
            ("Sentences", "sentences"),
            ("Paragraphs", "paragraphs"),
            ("Reading time", "reading_time"),
            ("Long sentences", "long_sentences"),
            ("Wording flags", "wording_flags"),
        )
        for index, (title, key) in enumerate(metric_specs):
            card = QFrame()
            card.setObjectName("entryBand")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 9, 12, 9)
            label = QLabel(title)
            label.setObjectName("mutedLabel")
            card_layout.addWidget(label)
            value = QLabel("--")
            value.setStyleSheet("font-size: 14pt; font-weight: 700;")
            value.setAccessibleName(title)
            card_layout.addWidget(value)
            self.analytics_metrics[key] = value
            metrics.addWidget(card, index // 2, index % 2)
        content_layout.addLayout(metrics)

        flags_title = QLabel("Needs attention")
        flags_title.setObjectName("sectionTitle")
        content_layout.addWidget(flags_title)
        self.analytics_flags = QLabel("Analyze the document to see wording and sentence flags.")
        self.analytics_flags.setObjectName("mutedLabel")
        self.analytics_flags.setWordWrap(True)
        self.analytics_flags.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        content_layout.addWidget(self.analytics_flags)

        guidance_title = QLabel("Improvement guide")
        guidance_title.setObjectName("sectionTitle")
        content_layout.addWidget(guidance_title)
        self.analytics_guidance = QLabel("No guidance yet.")
        self.analytics_guidance.setWordWrap(True)
        self.analytics_guidance.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        content_layout.addWidget(self.analytics_guidance)
        content_layout.addStretch(1)

        self.analytics_scroll.setWidget(content)
        root.addWidget(self.analytics_scroll, 1)
        self.analytics_status = QLabel("Load a document to begin.")
        self.analytics_status.setObjectName("mutedLabel")
        self.analytics_status.setWordWrap(True)
        root.addWidget(self.analytics_status)
        return page

    def _build_assistant_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 10, 0, 0)
        root.setSpacing(8)

        heading = QHBoxLayout()
        self.assistant_context = QLabel("Uses the current Editor draft.")
        self.assistant_context.setObjectName("mutedLabel")
        self.assistant_context.setWordWrap(True)
        heading.addWidget(self.assistant_context, 1)
        privacy = QLabel("LOCAL | REVIEW REQUIRED")
        privacy.setObjectName("privacyBadge")
        heading.addWidget(privacy)
        root.addLayout(heading)

        action_grid = QGridLayout()
        action_grid.setHorizontalSpacing(7)
        action_grid.setVerticalSpacing(7)
        self.assistant_command_buttons: dict[str, QPushButton] = {}
        for index, command in enumerate(COMMANDS):
            button = QPushButton(command)
            button.setProperty("segment", True)
            button.setToolTip(COMMANDS[command])
            button.clicked.connect(
                lambda _checked=False, value=command: self._run_assistant_command(value)
            )
            action_grid.addWidget(button, index // 4, index % 4)
            self.assistant_command_buttons[command] = button
        root.addLayout(action_grid)

        options = QHBoxLayout()
        options.addWidget(QLabel("Profile"))
        self.assistant_profile_combo = QComboBox()
        self.assistant_profile_combo.addItems(PROFILES)
        options.addWidget(self.assistant_profile_combo)
        self.assistant_language_edit = QLineEdit()
        self.assistant_language_edit.setPlaceholderText(
            "Translation language (only for Translate)"
        )
        options.addWidget(self.assistant_language_edit, 1)
        root.addLayout(options)

        self.assistant_instruction_edit = QLineEdit()
        self.assistant_instruction_edit.setPlaceholderText(
            "Optional direction, such as: keep under 80 words"
        )
        root.addWidget(self.assistant_instruction_edit)

        result_label = QLabel("Suggestion - edit before use")
        result_label.setObjectName("fieldHeading")
        root.addWidget(result_label)
        self.assistant_result_edit = QPlainTextEdit()
        self.assistant_result_edit.setPlaceholderText(
            "A local suggestion appears here. Nothing changes until you choose an action."
        )
        self.assistant_result_edit.textChanged.connect(
            self._refresh_assistant_decision_buttons
        )
        root.addWidget(self.assistant_result_edit, 1)

        decisions = QHBoxLayout()
        self.assistant_apply_button = QPushButton("Apply to draft")
        self.assistant_apply_button.setObjectName("primaryButton")
        self.assistant_apply_button.clicked.connect(self._apply_assistant_result)
        decisions.addWidget(self.assistant_apply_button)
        self.assistant_stage_button = QPushButton("Stage private paste")
        self.assistant_stage_button.clicked.connect(self._stage_assistant_result)
        decisions.addWidget(self.assistant_stage_button)
        self.assistant_reject_button = QPushButton("Reject")
        self.assistant_reject_button.setObjectName("dangerButton")
        self.assistant_reject_button.clicked.connect(self._reject_assistant_result)
        decisions.addWidget(self.assistant_reject_button)
        root.addLayout(decisions)

        self.assistant_status = QLabel(
            "Choose an action. The draft will not change automatically."
        )
        self.assistant_status.setObjectName("mutedLabel")
        self.assistant_status.setWordWrap(True)
        root.addWidget(self.assistant_status)
        self._refresh_assistant_decision_buttons()
        return page

    def _configure_accessibility(self) -> None:
        set_accessible(
            self,
            "Local document workspace",
            "Create, edit, export, analyze, and restore local documents with immutable revisions.",
        )
        set_accessible(self.document_combo, "Saved local documents")
        set_accessible(self.title_edit, "Document title")
        set_accessible(self.editor, "Document editor")
        set_accessible(self.status, "Document status", self.status.text())
        set_accessible(
            self.view_tabs,
            "Document editor and revisions",
            "Switch between the document editor, immutable revisions, local analytics, and writing assistant.",
        )
        set_accessible(self.revision_table, "Document revisions")
        set_accessible(self.revision_preview, "Selected revision preview")
        set_accessible(
            self.analytics_button,
            "Analyze document locally",
            "Refresh deterministic document metrics without AI or a network.",
        )
        set_accessible(self.analytics_score, "Document readability score")
        set_accessible(self.analytics_flags, "Document wording and sentence flags")
        set_accessible(self.analytics_guidance, "Document improvement guide")
        set_accessible(self.analytics_status, "Document analytics status")
        set_accessible(
            self.assistant_context,
            "Assistant draft context",
            "The assistant uses the current visible draft in the Editor tab.",
        )
        set_accessible(self.assistant_profile_combo, "Writing profile")
        set_accessible(self.assistant_language_edit, "Translation language")
        set_accessible(self.assistant_instruction_edit, "Optional writing direction")
        set_accessible(self.assistant_result_edit, "Editable local writing suggestion")
        set_accessible(
            self.assistant_apply_button,
            "Apply suggestion to draft",
            "Replace the current draft only. This does not save a revision.",
        )
        set_accessible(
            self.assistant_stage_button,
            "Stage suggestion for private paste",
            "Send the suggestion to Manuscript's private paste buffer without the Windows clipboard.",
        )
        set_accessible(self.assistant_status, "Document assistant status")

    def refresh_documents(self, *, select_id: int | None = None) -> None:
        documents = self.workspace.list_documents()
        if select_id is None:
            select_id = self.current_document_id
        self._loading = True
        try:
            self.document_combo.clear()
            for document in documents:
                self.document_combo.addItem(document.title, document.id)
            selected_index = self.document_combo.findData(select_id) if select_id is not None else -1
            if selected_index < 0 and documents:
                selected_index = 0
            self.document_combo.setCurrentIndex(selected_index)
        finally:
            self._loading = False
        if selected_index >= 0:
            self._load_document(int(self.document_combo.itemData(selected_index)))
        else:
            self._clear_document()

    def confirm_close(self) -> bool:
        return self._confirm_unsaved("Quit Manuscript?")

    def save_document(self) -> bool:
        if self.current_document_id is None:
            return False
        try:
            current = self.workspace.open_document(self.current_document_id)
            if self.loaded_revision is not None and current.current_revision != self.loaded_revision:
                raise RuntimeError(
                    "This document changed after it was opened. Reopen it before saving."
                )
            saved = self.workspace.update_document(
                self.current_document_id,
                self.editor.toPlainText(),
                title=self.title_edit.text(),
                label="Saved in editor",
                expected_revision=self.loaded_revision,
            )
        except (LookupError, RuntimeError, TypeError, ValueError) as exc:
            QMessageBox.warning(self, "Save document", str(exc))
            return False
        self.refresh_documents(select_id=saved.id)
        self._set_status(f"Saved locally as revision {saved.current_revision}.")
        return True

    def _new_document(self) -> None:
        if not self._confirm_unsaved("Create a new document?"):
            return
        try:
            document = self.workspace.create_document("Untitled document")
        except (TypeError, ValueError) as exc:
            QMessageBox.warning(self, "New document", str(exc))
            return
        self.refresh_documents(select_id=document.id)
        self.title_edit.selectAll()
        self.title_edit.setFocus()

    def _import_document(self) -> None:
        if not self._confirm_unsaved("Import another document?"):
            return
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Import local text document",
            "",
            "Text documents (*.txt *.md)",
        )
        if not path:
            return
        try:
            source = _validated_import_path(path)
            content = source.read_text(encoding="utf-8")
            document = self.workspace.create_document(
                source.stem,
                content,
                label=f"Imported {source.name}",
            )
        except (OSError, TypeError, UnicodeError, ValueError) as exc:
            QMessageBox.warning(self, "Import document", str(exc))
            return
        self.refresh_documents(select_id=document.id)
        self._set_status("Imported into the local workspace. The source file was not changed.")

    def _export_document(self) -> None:
        if self.current_document_id is None:
            return
        if self._dirty:
            answer = QMessageBox.question(
                self,
                "Unsaved document",
                "Save the current changes before exporting? Discard exports the last saved revision.",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if answer == QMessageBox.StandardButton.Save:
                if not self.save_document():
                    return
            elif answer == QMessageBox.StandardButton.Discard:
                self._load_document(self.current_document_id)
            else:
                return
        suggested = _safe_export_name(self.title_edit.text()) + ".txt"
        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export local document",
            suggested,
            "Text document (*.txt);;Markdown document (*.md)",
        )
        if not path:
            return
        try:
            destination = self.workspace.export_document(
                self.current_document_id,
                path,
                overwrite=True,
            )
        except (OSError, LookupError, TypeError, ValueError) as exc:
            QMessageBox.warning(self, "Export document", str(exc))
            return
        self._set_status(f"Exported locally to {destination}.")

    def _document_selected(self, index: int) -> None:
        if self._loading or index < 0:
            return
        selected_id = int(self.document_combo.itemData(index))
        if selected_id == self.current_document_id:
            return
        previous_id = self.current_document_id
        if not self._confirm_unsaved("Open another document?"):
            self._loading = True
            try:
                self.document_combo.setCurrentIndex(self.document_combo.findData(previous_id))
            finally:
                self._loading = False
            return
        self.refresh_documents(select_id=selected_id)

    def _load_document(self, document_id: int) -> None:
        try:
            document = self.workspace.open_document(document_id)
        except LookupError as exc:
            QMessageBox.warning(self, "Open document", str(exc))
            self.refresh_documents()
            return
        self._loading = True
        try:
            self.current_document_id = document.id
            self.loaded_revision = document.current_revision
            self.title_edit.setText(document.title)
            self.editor.setPlainText(document.content)
        finally:
            self._loading = False
        self._dirty = False
        self._set_document_controls(True)
        self.save_button.setEnabled(False)
        self._set_status(f"Saved locally as revision {document.current_revision}.")
        self._refresh_revisions(document)
        self._refresh_analytics(announce_change=False)
        self._reset_assistant_for_document()

    def _clear_document(self) -> None:
        self._loading = True
        try:
            self.current_document_id = None
            self.loaded_revision = None
            self.title_edit.clear()
            self.editor.clear()
            self.revision_table.setRowCount(0)
            self.revision_preview.clear()
        finally:
            self._loading = False
        self._dirty = False
        self._clear_analytics()
        self._reset_assistant_for_document("Load a document to use the assistant.")
        self._set_document_controls(False)
        self._set_status("No document selected. Choose New or Import text.")

    def _refresh_revisions(self, document: LocalDocument) -> None:
        revisions = self.workspace.list_revisions(document.id)
        self.revision_table.setRowCount(len(revisions))
        for row_index, revision in enumerate(revisions):
            revision_item = QTableWidgetItem(str(revision.revision_number))
            revision_item.setData(Qt.ItemDataRole.UserRole, revision.revision_number)
            self.revision_table.setItem(row_index, 0, revision_item)
            self.revision_table.setItem(row_index, 1, QTableWidgetItem(_display_time(revision.created_at)))
            self.revision_table.setItem(row_index, 2, QTableWidgetItem(revision.label))
        if revisions:
            self.revision_table.selectRow(0)
        else:
            self.revision_preview.clear()
            self.restore_button.setEnabled(False)

    def _preview_revision(self) -> None:
        revision_number = self._selected_revision_number()
        if self.current_document_id is None or revision_number is None:
            self.revision_preview.clear()
            self.restore_button.setEnabled(False)
            return
        try:
            revision = self.workspace.read_revision(self.current_document_id, revision_number)
        except LookupError as exc:
            self.revision_preview.setPlainText(str(exc))
            self.restore_button.setEnabled(False)
            return
        self.revision_preview.setPlainText(revision.content)
        self.restore_button.setEnabled(revision_number != self.loaded_revision)

    def _restore_revision(self) -> None:
        if self.current_document_id is None:
            return
        revision_number = self._selected_revision_number()
        if revision_number is None or revision_number == self.loaded_revision:
            return
        if not self._confirm_unsaved("Restore the selected revision?"):
            return
        try:
            current = self.workspace.open_document(self.current_document_id)
            if self.loaded_revision is not None and current.current_revision != self.loaded_revision:
                raise RuntimeError(
                    "This document changed after it was opened. Reopen it before restoring."
                )
            answer = QMessageBox.question(
                self,
                "Restore revision",
                f"Restore revision {revision_number}? A new revision will be added; old revisions stay intact.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            restored = self.workspace.restore_revision(
                self.current_document_id,
                revision_number,
                expected_revision=self.loaded_revision,
            )
        except (LookupError, RuntimeError, TypeError, ValueError) as exc:
            QMessageBox.warning(self, "Restore revision", str(exc))
            return
        self.refresh_documents(select_id=restored.id)
        self._set_status(
            f"Restored revision {revision_number} as new revision {restored.current_revision}."
        )

    def _selected_revision_number(self) -> int | None:
        row = self.revision_table.currentRow()
        item = self.revision_table.item(row, 0) if row >= 0 else None
        return int(item.data(Qt.ItemDataRole.UserRole)) if item is not None else None

    def _mark_dirty(self) -> None:
        if self._loading or self.current_document_id is None:
            return
        self._dirty = True
        self.save_button.setEnabled(True)
        self._set_status("Unsaved changes. Save revision to keep them.")

    def _analyze_clicked(self) -> None:
        self._refresh_analytics(announce_change=True)

    def _refresh_analytics(self, *, announce_change: bool) -> None:
        try:
            report = analyze_document(self.editor.toPlainText())
        except ValueError as exc:
            self._clear_analytics(str(exc))
            return
        self.analytics_last_report = report
        self.analytics_score.setText(f"{report.readability_score} / 100")
        self.analytics_score.setAccessibleDescription(
            f"{report.readability_score} out of 100, {report.readability_label}"
        )
        self.analytics_score_label.setText(report.readability_label)
        self.analytics_score_bar.setValue(report.readability_score)
        values = {
            "words": str(report.word_count),
            "sentences": str(report.sentence_count),
            "paragraphs": str(report.paragraph_count),
            "reading_time": report.reading_time_label,
            "long_sentences": str(report.long_sentence_count),
            "wording_flags": str(report.filler_count + report.weak_wording_count),
        }
        for key, value in values.items():
            self.analytics_metrics[key].setText(value)
            self.analytics_metrics[key].setAccessibleDescription(value)
        flag_lines: list[str] = []
        if report.long_sentence_previews:
            flag_lines.append(
                "Long: " + " | ".join(report.long_sentence_previews)
            )
        if report.filler_flags:
            flag_lines.append("Fillers: " + _display_flags(report.filler_flags))
        if report.weak_wording_flags:
            flag_lines.append("Weak wording: " + _display_flags(report.weak_wording_flags))
        self.analytics_flags.setText(
            "\n".join(flag_lines) if flag_lines else "No common wording or long-sentence flags."
        )
        self.analytics_guidance.setText(
            "\n".join(f"{index}. {item}" for index, item in enumerate(report.guidance, 1))
        )
        update_status_label(
            self.analytics_status,
            "Analysis refreshed locally for the current draft.",
            announce_change=announce_change,
        )

    def _clear_analytics(self, message: str = "Load a document to begin.") -> None:
        self.analytics_last_report = None
        self.analytics_score.setText("-- / 100")
        self.analytics_score.setAccessibleDescription("Not analyzed")
        self.analytics_score_label.setText("Not analyzed")
        self.analytics_score_bar.setValue(0)
        for value in self.analytics_metrics.values():
            value.setText("--")
            value.setAccessibleDescription("Not analyzed")
        self.analytics_flags.setText("Analyze the document to see wording and sentence flags.")
        self.analytics_guidance.setText("No guidance yet.")
        update_status_label(self.analytics_status, message, announce_change=False)

    def _run_assistant_command(self, command: str) -> None:
        if self._assistant_busy:
            return
        source = self.editor.toPlainText()
        if not source.strip():
            update_status_label(
                self.assistant_status,
                "Write or load a draft first.",
                assertive=True,
            )
            return
        language = self.assistant_language_edit.text().strip()
        if command == "Translate" and not language:
            update_status_label(
                self.assistant_status,
                "Enter a translation language first.",
                assertive=True,
            )
            self.assistant_language_edit.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        try:
            endpoint = getattr(self.writing_assistant, "endpoint", None)
            validate_ollama_endpoint(endpoint)
            model = str(self._writing_model_provider() or "").strip()
            if not model:
                raise ValueError("Local writing AI is unavailable. Open Settings to repair it.")
            protected_terms = tuple(str(value) for value in self._protected_terms_provider())
            timeout = float(self._writing_timeout_provider())
            if not 2.0 <= timeout <= 300.0:
                raise ValueError("Local writing timeout must be between 2 and 300 seconds.")
        except (TypeError, ValueError) as exc:
            update_status_label(
                self.assistant_status,
                user_ai_status(str(exc)),
                assertive=True,
            )
            return

        self._assistant_request_id += 1
        request_id = self._assistant_request_id
        self._assistant_source_text = source
        profile = self.assistant_profile_combo.currentText()
        instruction = self.assistant_instruction_edit.text()
        self._set_assistant_busy(True, command)

        def work() -> None:
            try:
                result = self.writing_assistant.run(
                    source,
                    command=command,
                    model=model,
                    instruction=instruction,
                    language=language,
                    profile=profile,
                    protected_terms=protected_terms,
                    timeout=timeout,
                )
                payload = (
                    request_id,
                    source,
                    True,
                    result.text,
                    f"{command} ready. Review or edit it before use.",
                )
            except (RuntimeError, TypeError, ValueError) as exc:
                payload = (request_id, source, False, "", str(exc))
            self.assistant_result_ready.emit(payload)

        threading.Thread(
            target=work,
            daemon=True,
            name=f"echo-link-document-{command.casefold()}",
        ).start()

    def _handle_assistant_result(self, payload) -> None:
        request_id, source, ok, text, message = payload
        if (
            request_id != self._assistant_request_id
            or source != self._assistant_source_text
            or source != self.editor.toPlainText()
        ):
            return
        self._set_assistant_busy(False)
        message = user_ai_status(message)
        update_status_label(self.assistant_status, message, assertive=not ok)
        if not ok:
            return
        self.assistant_result_edit.setPlainText(text)
        self.assistant_result_edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def _set_assistant_busy(self, busy: bool, command: str = "") -> None:
        self._assistant_busy = bool(busy)
        for button in self.assistant_command_buttons.values():
            button.setEnabled(not busy)
        self.assistant_profile_combo.setEnabled(not busy)
        self.assistant_language_edit.setEnabled(not busy)
        self.assistant_instruction_edit.setEnabled(not busy)
        if busy:
            self.assistant_result_edit.clear()
            update_status_label(self.assistant_status, f"Running {command} locally...")
        self._refresh_assistant_decision_buttons()

    def _assistant_draft_changed(self) -> None:
        text = self.editor.toPlainText()
        self._update_assistant_context(text)
        if self._assistant_suppress_draft_change:
            return
        if (
            self._assistant_busy
            or self._assistant_source_text
            or self.assistant_result_edit.toPlainText()
        ):
            self._assistant_request_id += 1
            self._assistant_busy = False
            self._assistant_source_text = ""
            self.assistant_result_edit.clear()
            for button in self.assistant_command_buttons.values():
                button.setEnabled(True)
            self.assistant_profile_combo.setEnabled(True)
            self.assistant_language_edit.setEnabled(True)
            self.assistant_instruction_edit.setEnabled(True)
            self._refresh_assistant_decision_buttons()
            update_status_label(
                self.assistant_status,
                "Draft changed. Run the assistant again for current text.",
            )

    def _apply_assistant_result(self) -> None:
        result = self.assistant_result_edit.toPlainText().strip()
        if not self._assistant_result_is_current() or not result:
            return
        self._assistant_request_id += 1
        self._assistant_suppress_draft_change = True
        try:
            self.editor.setPlainText(result)
        finally:
            self._assistant_suppress_draft_change = False
        self._assistant_source_text = ""
        self.assistant_result_edit.clear()
        self._update_assistant_context(result)
        self._refresh_assistant_decision_buttons()
        update_status_label(
            self.assistant_status,
            "Applied to the unsaved draft. Save revision only when you are ready.",
        )
        self.view_tabs.setCurrentIndex(0)
        self.editor.setFocus(Qt.FocusReason.OtherFocusReason)

    def _stage_assistant_result(self) -> None:
        result = self.assistant_result_edit.toPlainText().strip()
        if not self._assistant_result_is_current() or not result:
            return
        self.assistant_stage_requested.emit(result)
        update_status_label(
            self.assistant_status,
            "Sent to Manuscript private paste. Windows clipboard unchanged.",
        )

    def _reject_assistant_result(self) -> None:
        self._assistant_request_id += 1
        self._assistant_busy = False
        self._assistant_source_text = ""
        self.assistant_result_edit.clear()
        for button in self.assistant_command_buttons.values():
            button.setEnabled(True)
        self.assistant_profile_combo.setEnabled(True)
        self.assistant_language_edit.setEnabled(True)
        self.assistant_instruction_edit.setEnabled(True)
        self._refresh_assistant_decision_buttons()
        update_status_label(
            self.assistant_status,
            "Suggestion rejected. The draft was not changed.",
        )

    def _assistant_result_is_current(self) -> bool:
        return bool(self._assistant_source_text) and (
            self._assistant_source_text == self.editor.toPlainText()
        )

    def _refresh_assistant_decision_buttons(self) -> None:
        enabled = (
            not self._assistant_busy
            and bool(self.assistant_result_edit.toPlainText().strip())
            and self._assistant_result_is_current()
        )
        self.assistant_apply_button.setEnabled(enabled)
        self.assistant_stage_button.setEnabled(enabled)

    def _reset_assistant_for_document(
        self,
        message: str = "Choose an action. The draft will not change automatically.",
    ) -> None:
        self._assistant_request_id += 1
        self._assistant_busy = False
        self._assistant_source_text = ""
        self.assistant_result_edit.clear()
        for button in self.assistant_command_buttons.values():
            button.setEnabled(True)
        self.assistant_profile_combo.setEnabled(True)
        self.assistant_language_edit.setEnabled(True)
        self.assistant_instruction_edit.setEnabled(True)
        self._update_assistant_context(self.editor.toPlainText())
        self._refresh_assistant_decision_buttons()
        update_status_label(self.assistant_status, message, announce_change=False)

    def _update_assistant_context(self, text: str) -> None:
        words = len(text.split())
        self.assistant_context.setText(
            f"Uses the current Editor draft: {words} word{'s' if words != 1 else ''}."
        )

    def _confirm_unsaved(self, question: str) -> bool:
        if not self._dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Unsaved document",
            f"{question} Save the current changes first?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save_document()
        return answer == QMessageBox.StandardButton.Discard

    def _set_document_controls(self, enabled: bool) -> None:
        self.export_button.setEnabled(enabled)
        self.save_button.setEnabled(enabled and self._dirty)
        self.title_edit.setEnabled(enabled)
        self.editor.setEnabled(enabled)
        self.view_tabs.setEnabled(enabled)

    def _set_status(self, text: str) -> None:
        update_status_label(self.status, text)


def _validated_import_path(value: str | Path) -> Path:
    raw = str(value)
    if is_network_path(raw):
        raise ValueError("Network import paths are not allowed.")
    path = Path(value)
    if path.is_symlink():
        raise ValueError("Symbolic-link imports are not allowed.")
    resolved = path.resolve(strict=True)
    if is_network_path(resolved):
        raise ValueError("Network import paths are not allowed.")
    if resolved.suffix.casefold() not in _ALLOWED_IMPORT_SUFFIXES:
        raise ValueError("Import a .txt or .md file.")
    if not resolved.is_file():
        raise ValueError("Choose a local text file.")
    if resolved.stat().st_size > _MAX_IMPORT_BYTES:
        raise ValueError("Text documents must be 5 MB or smaller.")
    if b"\x00" in resolved.read_bytes()[:8192]:
        raise ValueError("Binary files cannot be imported.")
    return resolved


def _safe_export_name(value: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (value or "").strip())
    return safe.rstrip(" .")[:120] or "Manuscript document"


def _display_time(value: str) -> str:
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%b %d, %I:%M %p")
    except ValueError:
        return value


def _display_flags(flags) -> str:
    return ", ".join(
        flag.term + (f" ({flag.count})" if flag.count > 1 else "")
        for flag in flags
    )


__all__ = ["DocumentEditorPanel"]
