from __future__ import annotations

import threading
from collections.abc import Callable, Sequence

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from echo_link_app.accessibility import set_accessible, update_status_label
from echo_link_app.ai_status import user_ai_status
from echo_link_core.documents import DocumentWorkspace
from echo_link_core.drafting import DraftGenerationError, DraftTemplate, LocalDraftAssistant


class DraftsPanel(QWidget):
    """Review-first local draft generation. It never inserts or saves automatically."""

    generation_finished = Signal(object)
    stage_private_paste_requested = Signal(str)

    def __init__(
        self,
        workspace: DocumentWorkspace,
        *,
        model_provider: Callable[[], str],
        protected_terms_provider: Callable[[], Sequence[str]],
        timeout_provider: Callable[[], float] | None = None,
        assistant: LocalDraftAssistant | None = None,
        inference=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self._model_provider = model_provider
        self._protected_terms_provider = protected_terms_provider
        self._timeout_provider = timeout_provider or (lambda: 60.0)
        self.assistant = assistant or LocalDraftAssistant(inference=inference)
        self._request_id = 0
        self._busy_request: int | None = None
        self._saved_text = ""

        self._build_ui()
        self._configure_accessibility()
        self.generation_finished.connect(self._handle_generation_result)
        self.refresh_local_status()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 8, 6, 8)
        layout.setSpacing(10)

        title = QLabel("Draft generation")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        description = QLabel(
            "Build a first draft from facts you provide. Manuscript uses only local AI. "
            "Review every result before saving or staging it."
        )
        description.setObjectName("mutedLabel")
        description.setWordWrap(True)
        layout.addWidget(description)

        local_band = QFrame()
        local_band.setObjectName("entryBand")
        local_layout = QVBoxLayout(local_band)
        local_layout.setContentsMargins(12, 9, 12, 9)
        self.local_status = QLabel()
        self.local_status.setWordWrap(True)
        local_layout.addWidget(self.local_status)
        layout.addWidget(local_band)

        inputs = QGridLayout()
        inputs.setHorizontalSpacing(10)
        inputs.setVerticalSpacing(6)
        template_label = QLabel("Draft type")
        self.template_combo = QComboBox()
        self.template_combo.addItems([template.value for template in DraftTemplate])
        template_label.setBuddy(self.template_combo)
        inputs.addWidget(template_label, 0, 0)
        inputs.addWidget(self.template_combo, 1, 0)
        direction_label = QLabel("Optional direction")
        self.direction_edit = QPlainTextEdit()
        self.direction_edit.setPlaceholderText("Example: concise, neutral tone, with short sections")
        self.direction_edit.setMaximumHeight(76)
        direction_label.setBuddy(self.direction_edit)
        inputs.addWidget(direction_label, 0, 1)
        inputs.addWidget(self.direction_edit, 1, 1)
        inputs.setColumnStretch(1, 1)
        layout.addLayout(inputs)

        facts_label = QLabel("Required facts and context")
        self.facts_edit = QPlainTextEdit()
        self.facts_edit.setPlaceholderText(
            "Add the names, dates, decisions, claims, code, and other facts the draft may use."
        )
        self.facts_edit.setMinimumHeight(100)
        self.facts_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        facts_label.setBuddy(self.facts_edit)
        layout.addWidget(facts_label)
        layout.addWidget(self.facts_edit, 1)

        generate_row = QHBoxLayout()
        self.generate_button = QPushButton("Generate locally")
        self.generate_button.setObjectName("primaryButton")
        self.generate_button.clicked.connect(self.generate)
        generate_row.addWidget(self.generate_button)
        generate_row.addStretch(1)
        layout.addLayout(generate_row)

        result_label = QLabel("Editable review result")
        self.result_edit = QPlainTextEdit()
        self.result_edit.setPlaceholderText("The local draft will appear here. Nothing is saved or inserted.")
        self.result_edit.setMinimumHeight(120)
        self.result_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        result_label.setBuddy(self.result_edit)
        layout.addWidget(result_label)
        layout.addWidget(self.result_edit, 1)

        actions = QGridLayout()
        actions.setHorizontalSpacing(8)
        actions.setVerticalSpacing(8)
        self.save_button = QPushButton("Save to Documents")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save_to_documents)
        actions.addWidget(self.save_button, 0, 0)
        self.stage_button = QPushButton("Stage private paste")
        self.stage_button.setEnabled(False)
        self.stage_button.clicked.connect(self.stage_private_paste)
        actions.addWidget(self.stage_button, 0, 1)
        self.reject_button = QPushButton("Reject")
        self.reject_button.setObjectName("dangerButton")
        self.reject_button.clicked.connect(self.reject)
        actions.addWidget(self.reject_button, 0, 2)
        actions.setColumnStretch(3, 1)
        layout.addLayout(actions)

        self.status = QLabel("Add facts to begin. Nothing is saved, inserted, or sent automatically.")
        self.status.setObjectName("mutedLabel")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.template_combo.currentIndexChanged.connect(self._invalidate_generated_result)
        self.direction_edit.textChanged.connect(self._invalidate_generated_result)
        self.facts_edit.textChanged.connect(self._invalidate_generated_result)
        self.result_edit.textChanged.connect(self._refresh_review_actions)

    def _configure_accessibility(self) -> None:
        set_accessible(
            self,
            "Local draft generation",
            "Creates review-only drafts with local AI.",
        )
        set_accessible(self.local_status, "Draft AI privacy status", self.local_status.text())
        set_accessible(self.template_combo, "Draft template")
        set_accessible(self.direction_edit, "Optional draft direction")
        set_accessible(self.facts_edit, "Required draft facts and context")
        set_accessible(self.generate_button, "Generate draft locally")
        set_accessible(self.result_edit, "Editable draft review result")
        set_accessible(
            self.save_button,
            "Save draft to Documents",
            "Creates a local document only when pressed.",
        )
        set_accessible(
            self.stage_button,
            "Stage draft for private paste",
            "Stores the reviewed draft in Manuscript memory without using the Windows clipboard.",
        )
        set_accessible(self.reject_button, "Reject draft")
        set_accessible(self.status, "Draft generation status", self.status.text())

    def refresh_local_status(self) -> None:
        message = "LOCAL ONLY | AI runs on this computer | Review required"
        self.local_status.setText(message)
        self.local_status.setAccessibleDescription(message)

    def generate(self) -> None:
        facts = self.facts_edit.toPlainText()
        instruction = self.direction_edit.toPlainText()
        template = self.template_combo.currentText()
        model = str(self._model_provider() or "").strip()
        try:
            protected_terms = tuple(str(value) for value in self._protected_terms_provider())
            timeout = max(2.0, min(300.0, float(self._timeout_provider())))
        except (TypeError, ValueError) as exc:
            update_status_label(self.status, str(exc), assertive=True)
            return

        self._request_id += 1
        request_id = self._request_id
        self._busy_request = request_id
        self._saved_text = ""
        self.result_edit.clear()
        self._set_review_actions(False)
        self.generate_button.setEnabled(False)
        update_status_label(self.status, f"Generating {template} locally. Review will be required.")

        def work() -> None:
            try:
                result = self.assistant.generate(
                    template,
                    facts=facts,
                    model=model,
                    instruction=instruction,
                    protected_terms=protected_terms,
                    timeout=timeout,
                )
                payload = (request_id, True, result.text, "Draft ready. Edit and review it before use.")
            except (DraftGenerationError, RuntimeError, TypeError, ValueError) as exc:
                payload = (request_id, False, "", str(exc))
            self.generation_finished.emit(payload)

        threading.Thread(target=work, daemon=True, name="echo-link-local-draft").start()

    def _handle_generation_result(self, payload: object) -> None:
        request_id, ok, text, message = payload
        if request_id != self._request_id:
            return
        self._busy_request = None
        self.generate_button.setEnabled(True)
        message = user_ai_status(message)
        update_status_label(self.status, message, assertive=not ok)
        if not ok:
            self.result_edit.clear()
            self._set_review_actions(False)
            return
        self.result_edit.setPlainText(text)
        self.result_edit.setFocus()
        self._refresh_review_actions()

    def save_to_documents(self) -> None:
        text = self.result_edit.toPlainText().strip()
        if not text or self._busy_request is not None:
            return
        try:
            document = self.workspace.create_document(
                self._document_title(text),
                text,
                label=f"Created from {self.template_combo.currentText()} draft",
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            QMessageBox.warning(self, "Save draft", str(exc))
            update_status_label(self.status, "Draft was not saved.", assertive=True)
            return
        self._saved_text = text
        self._refresh_review_actions()
        update_status_label(
            self.status,
            f"Saved locally to Documents as {document.title}. Nothing was inserted or sent.",
        )

    def stage_private_paste(self) -> None:
        text = self.result_edit.toPlainText().strip()
        if not text or self._busy_request is not None:
            return
        self.stage_private_paste_requested.emit(text)
        update_status_label(
            self.status,
            "Draft staged in Manuscript memory. Windows clipboard unchanged.",
        )

    def reject(self) -> None:
        self._request_id += 1
        self._busy_request = None
        self._saved_text = ""
        self.result_edit.clear()
        self.generate_button.setEnabled(True)
        self._set_review_actions(False)
        update_status_label(
            self.status,
            "Draft rejected. Nothing was saved, inserted, or sent.",
        )

    def _invalidate_generated_result(self) -> None:
        if self._busy_request is None and not self.result_edit.toPlainText():
            self.refresh_local_status()
            return
        self._request_id += 1
        self._busy_request = None
        self._saved_text = ""
        self.result_edit.clear()
        self.generate_button.setEnabled(True)
        self._set_review_actions(False)
        update_status_label(self.status, "Inputs changed. Generate a new local draft.")
        self.refresh_local_status()

    def _refresh_review_actions(self) -> None:
        text = self.result_edit.toPlainText().strip()
        enabled = bool(text) and self._busy_request is None
        self.save_button.setEnabled(enabled and text != self._saved_text)
        self.stage_button.setEnabled(enabled)

    def _set_review_actions(self, enabled: bool) -> None:
        self.save_button.setEnabled(enabled)
        self.stage_button.setEnabled(enabled)

    def _document_title(self, text: str) -> str:
        first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
        if first_line.casefold().startswith("subject:"):
            first_line = first_line[8:].strip()
        safe_line = " ".join(first_line.split())[:120]
        return f"{self.template_combo.currentText()} draft" + (
            f" - {safe_line}" if safe_line else ""
        )


__all__ = ["DraftsPanel"]
