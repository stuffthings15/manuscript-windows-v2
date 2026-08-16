from __future__ import annotations

from collections.abc import Callable
import ctypes
from dataclasses import replace
import logging
import math
import sys
import time

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QCloseEvent,
    QCursor,
    QFont,
    QFontMetrics,
    QIcon,
    QKeyEvent,
    QMouseEvent,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from echo_link_app.accessibility import set_accessible, update_status_label
from echo_link_app.ai_status import user_ai_status
from echo_link_app.review_spans import (
    FILTERS,
    _apply_all_sentence_suggestions,
    _apply_sentence_suggestion,
    _category_name,
    _group_sentence_suggestions,
    _shift_remaining_sentence_suggestions,
    _token_diff_opcodes,
)
from echo_link_app.theme import COLORS
from echo_link_app.branding import APP_NAME, ICON_PNG
from echo_link_core.paths import resource_path
from echo_link_core.selected_text import COMMANDS
from echo_link_core.polish_spelling import PolishRuleEngine
from echo_link_core.writing_suggestions import (
    WritingSuggestionEngine,
)


logger = logging.getLogger(__name__)


class _CurrentPageStack(QStackedWidget):
    """Let the frameless HUD shrink to its active disclosure level."""

    def sizeHint(self) -> QSize:
        current = self.currentWidget()
        return current.sizeHint() if current is not None else super().sizeHint()

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 0)


class RewritePalette(QDialog):
    """Progressive, focus-safe writing HUD for one captured selection."""

    command_requested = Signal(str, str)
    sentence_rewrite_requested = Signal(int, str, str, str)
    replace_requested = Signal(str)
    stage_requested = Signal(str)
    document_requested = Signal()
    settings_requested = Signal()
    unchanged = Signal()
    applied = Signal()
    rejected = Signal()
    target_changed = Signal(int, int)

    _BADGE_SIZE = QSize(36, 36)
    _LOADING_SIZE = QSize(292, 108)
    _CARD_SIZE = QSize(480, 520)
    _CARD_MAX_WIDTH = 760
    _CARD_MAX_HEIGHT = 820
    _ASSISTANT_SIZE = QSize(620, 660)
    # A cold local AI load may use 90 seconds, followed by up to 30 seconds
    # of generation. The HUD must not discard a valid late result early.
    _AI_TIMEOUT_MS = 120_000

    def __init__(
        self,
        parent=None,
        *,
        suggestion_engine=None,
        ai_proofread: bool = False,
    ):
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        super().__init__(parent, flags)
        self.setWindowTitle("Manuscript writing HUD")
        self.setModal(False)
        # Windows can freeze the last bitmap of a translucent top-level tool
        # window even while Qt accessibility data keeps changing. The HUD is
        # fully dark, so an opaque window gives reliable visual redraws.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self._preferred_size = QSize(self._ASSISTANT_SIZE)
        self._programmatic_resize = False
        self._user_view_sizes: dict[str, QSize] = {}
        self._busy = False
        self._closing_after_choice = False
        self._suggestion_engine = suggestion_engine or WritingSuggestionEngine()
        # The review card used to offer only the regex rules, so a misspelling
        # the rules do not name - animul, pett, befor, lernt, especialy - was
        # shown to the reader uncorrected even though the bundled dictionary
        # could fix every one. The engine behind a Proofread has always had
        # both; the card now uses the same pairing.
        #
        # Built only when the caller did not inject an engine, so tests that
        # pass a stub still exercise the stub alone.
        self._spelling_engine = (
            None if suggestion_engine is not None else PolishRuleEngine()
        )
        self._ai_proofread = bool(ai_proofread)
        self._ai_requested_ids: set[str] = set()
        self._ai_completed_ids: set[str] = set()
        self._ai_failed_ids: set[str] = set()
        # Sections resolved by the reader in THIS review: dismissed, or stepped
        # past deliberately with Next. An automatic step over a section whose
        # correction has not arrived is NOT resolution - it is deferral, and the
        # section comes back. Nothing distinguished the two, because both were
        # _review_index += 1, so the counter showed a cursor rather than
        # progress and climbed while nothing had been decided.
        self._review_resolved: set[str] = set()
        # Sections resolved by the reader in THIS review: dismissed, or stepped
        # past deliberately with Next. An automatic step over a section whose
        # correction has not arrived is NOT resolution - it is deferral, and
        # the section comes back. Nothing distinguished the two, because both
        # were _review_index += 1, so the counter showed a cursor rather than
        # progress and climbed while nothing had been decided.
        self._review_resolved: set[str] = set()
        self._ai_auto_retry_counts: dict[str, int] = {}
        self._ai_attempt_counter = 0
        self._active_ai_attempt_id = 0
        self._active_ai_suggestion_id = ""
        self._active_ai_background = False
        # All in-flight attempts (foreground plus background prefetch),
        # attempt id -> suggestion id. The foreground attempt additionally
        # drives the busy state, progress timer, and timeout UI.
        self._ai_attempts: dict[int, str] = {}
        # How many attempts may run at once. The host window raises this on
        # capable hardware; the default keeps historical single-flight
        # behavior everywhere else.
        self._ai_width_provider: Callable[[], int] = lambda: 1
        self._ai_ready_messages: dict[str, str] = {}
        self._ai_failure_messages: dict[str, str] = {}
        self._ai_started_at = 0.0
        self._ai_backend = "Checking GPU"
        self._ai_eta_seconds: float | None = None
        self._ai_request_started: dict[str, float] = {}
        self._ai_duration_samples: list[float] = []
        self._suggestions: tuple[object, ...] = ()
        self._visible_suggestions: tuple[object, ...] = ()
        self._review_suggestions: tuple[object, ...] = ()
        self._dismissed_ids: set[str] = set()
        self._review_index = 0
        self._review_progress_total = 0
        self._review_progress_completed = 0
        # Last counter string written to the log, so the progression is
        # recorded once per change rather than on every repaint.
        self._logged_progress = ""
        self._step_replacer: Callable[[str, object], bool] | None = None
        self._accepted_external_changes = False
        self._preview_base = ""
        self._updating_result = False
        self._automatic = False
        self._user_opened = False
        self._document_scope = False
        self._anchor: tuple[int, int, int, int] | None = None
        self._target_marking_enabled = False
        self._view = "badge"
        self._drag_offset: QPoint | None = None
        self._badge_press_origin: QPoint | None = None
        self._badge_drag_offset: QPoint | None = None
        self._badge_dragged = False
        self._user_positioned = False
        self._analysis_pending = False
        self._pulse_step = 0
        self._analysis_timer = QTimer(self)
        self._analysis_timer.setSingleShot(True)
        # Long enough that typing does not trigger it. The badge defers its
        # analysis so a burst of focus signals costs one review rather than
        # several, and at 180ms it was not deferring anything a person could
        # produce: measured 2026-08-16, three captures of a chat window 600 to
        # 900ms apart each completed a full review before the next arrived, so
        # the writer got a fresh card on every pause between words.
        #
        # Somebody who has stopped to look at the badge has stopped for longer
        # than this. Somebody still typing has not.
        self._analysis_timer.setInterval(1200)
        self._analysis_timer.timeout.connect(self._finish_automatic_analysis)
        # "No Issues: show a short positive status, then collapse" - the GUI
        # build manual, section 14. Long enough to read one line, short enough
        # that a document with nothing wrong does not leave a card sitting on
        # the writer's screen. Before this, a finished document re-offered
        # itself and the writer was left looking at "0 of 0" with no way
        # forward, which is what they reported on 2026-08-16.
        self._no_issues_timer = QTimer(self)
        self._no_issues_timer.setSingleShot(True)
        self._no_issues_timer.setInterval(3000)
        self._no_issues_timer.timeout.connect(self._collapse_after_no_issues)
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(90)
        self._pulse_timer.timeout.connect(self._pulse_badge)
        self._ai_timeout_timer = QTimer(self)
        self._ai_timeout_timer.setSingleShot(True)
        self._ai_timeout_timer.setInterval(self._AI_TIMEOUT_MS)
        self._ai_timeout_timer.timeout.connect(self._expire_ai_request)
        self._ai_progress_timer = QTimer(self)
        self._ai_progress_timer.setInterval(1_000)
        self._ai_progress_timer.timeout.connect(self._update_ai_progress)
        self._build_ui()
        self._resize_grip = QSizeGrip(self)
        self._resize_grip.setAccessibleName("Resize suggestion window")
        self._resize_grip.hide()
        self._apply_view_size()

    @property
    def result_text(self) -> str:
        return self.result_edit.toPlainText()

    @property
    def suggestions(self) -> tuple[object, ...]:
        return self._suggestions

    @property
    def view(self) -> str:
        return self._view

    @property
    def automatic(self) -> bool:
        return self._automatic

    @property
    def user_opened(self) -> bool:
        return self._user_opened

    def set_target_marking_enabled(self, enabled: bool) -> None:
        """Show range marking only while its source text still owns focus."""
        value = bool(enabled)
        if self._target_marking_enabled == value:
            return
        self._target_marking_enabled = value
        self._publish_current_target()

    def set_step_replacer(
        self,
        replacer: Callable[[str, object], bool] | None,
    ) -> None:
        self._step_replacer = replacer

    def set_ai_width_provider(self, provider: Callable[[], int] | None) -> None:
        """Let the host window scale concurrent AI prefetch to the hardware."""
        self._ai_width_provider = provider or (lambda: 1)

    def _ai_parallel_width(self) -> int:
        try:
            width = int(self._ai_width_provider())
        except Exception:
            width = 1
        return max(1, min(4, width))

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.stack = _CurrentPageStack()
        root.addWidget(self.stack)
        self.badge_page = self._build_badge_page()
        self.loading_page = self._build_loading_page()
        self.card_page = self._build_card_page()
        self.assistant_page = self._build_assistant_page()
        # Keep only the active page in the stack. Qt reserves geometry for
        # hidden stacked pages on Windows; the old assistant page therefore
        # left a large, clickable blank area below the compact card.
        self.stack.addWidget(self.badge_page)

        self.setStyleSheet(_HUD_STYLESHEET)
        self._configure_accessibility()
        self._refresh_decision_buttons()
        self._render_suggestions()

    def _build_badge_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("hudBadgePage")
        layout = QHBoxLayout(page)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        self.badge_button = QPushButton("...")
        self.badge_button.setObjectName("hudBadgeButton")
        self.badge_button.setMinimumSize(self._BADGE_SIZE)
        icon_path = resource_path(*ICON_PNG)
        if icon_path.is_file():
            self.badge_button.setIcon(QIcon(str(icon_path)))
            self.badge_button.setIconSize(QSize(22, 22))
        self.badge_button.clicked.connect(self._open_from_badge)
        # The badge is a button filling the whole window, so the window's own
        # drag handling never saw a press: mousePressEvent ignores presses that
        # land on a QPushButton, which is right for the card's controls and
        # wrong here, where the button is the only thing there is to grab.
        self.badge_button.installEventFilter(self)
        layout.addWidget(self.badge_button)
        return page

    def eventFilter(self, watched, event):
        if watched is getattr(self, "badge_button", None):
            self._filter_badge_drag(event)
        return super().eventFilter(watched, event)

    def _filter_badge_drag(self, event) -> None:
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                self._badge_press_origin = event.globalPosition().toPoint()
                self._badge_dragged = False
        elif kind == QEvent.Type.MouseMove:
            if self._badge_press_origin is None:
                return
            origin = self._badge_press_origin
            moved = event.globalPosition().toPoint() - origin
            if (
                not self._badge_dragged
                and max(abs(moved.x()), abs(moved.y()))
                < QApplication.startDragDistance()
            ):
                # Below the platform's drag threshold this is still a click,
                # not a drag. Without the threshold a badge could never be
                # pressed without moving a pixel and sliding away.
                return
            if not self._badge_dragged:
                self._badge_dragged = True
                self._badge_drag_offset = origin - self.frameGeometry().topLeft()
            if self._badge_drag_offset is not None:
                self.move(event.globalPosition().toPoint() - self._badge_drag_offset)
                self._user_positioned = True
        elif kind == QEvent.Type.MouseButtonRelease:
            self._badge_press_origin = None
            self._badge_drag_offset = None
            if self._badge_dragged:
                self._clamp_current_position()

    def _build_loading_page(self) -> QWidget:
        page = QFrame()
        page.setObjectName("hudLoading")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(5)

        header = QHBoxLayout()
        title = QLabel(APP_NAME)
        title.setObjectName("hudTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.loading_close_button = QPushButton("Close")
        self.loading_close_button.setObjectName("hudQuietButton")
        self.loading_close_button.setFixedHeight(30)
        self.loading_close_button.clicked.connect(self.reject)
        header.addWidget(self.loading_close_button)
        layout.addLayout(header)

        self.loading_status = QLabel("Proofreading locally...")
        self.loading_status.setObjectName("hudStatus")
        self.loading_status.setAccessibleName("Local AI proofreading status")
        layout.addWidget(self.loading_status)
        self.loading_progress = QProgressBar(page)
        self.loading_progress.setObjectName("hudSeriesProgress")
        self.loading_progress.setFixedHeight(8)
        self.loading_progress.setTextVisible(False)
        self.loading_progress.setRange(0, 1)
        self.loading_progress.setValue(0)
        self.loading_progress.hide()
        set_accessible(self.loading_progress, "Background AI section progress")
        layout.addWidget(self.loading_progress)
        self.loading_eta = QLabel("", page)
        self.loading_eta.setObjectName("hudExplanation")
        self.loading_eta.hide()
        set_accessible(self.loading_eta, "Background AI time estimate")
        layout.addWidget(self.loading_eta)
        return page

    def _build_card_page(self) -> QWidget:
        page = QFrame()
        page.setObjectName("hudCard")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 6, 8, 4)
        layout.setSpacing(3)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        header = QHBoxLayout()
        self.settings_button = QPushButton("⚙")
        self.settings_button.setObjectName("hudQuietButton")
        self.settings_button.setFixedSize(30, 30)
        self.settings_button.setToolTip("Open Manuscript settings")
        self.settings_button.clicked.connect(self.settings_requested.emit)
        header.addWidget(self.settings_button)
        title = QLabel(APP_NAME)
        title.setObjectName("hudTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.card_position = QLabel("0 of 0")
        self.card_position.setObjectName("hudCount")
        header.addWidget(self.card_position)
        self.close_button = QPushButton("×")
        self.close_button.setObjectName("hudQuietButton")
        self.close_button.setFixedSize(30, 30)
        self.close_button.clicked.connect(self.reject)
        header.addWidget(self.close_button)
        layout.addLayout(header)

        # Whole-series progress: how many sections the background AI has
        # finished preparing, and roughly how long the rest will take.
        self.series_progress = QProgressBar(page)
        self.series_progress.setObjectName("hudSeriesProgress")
        self.series_progress.setFixedHeight(8)
        self.series_progress.setTextVisible(False)
        self.series_progress.setRange(0, 1)
        self.series_progress.setValue(0)
        self.series_progress.hide()
        set_accessible(self.series_progress, "Background AI section progress")
        layout.addWidget(self.series_progress)
        self.series_eta = QLabel("", page)
        self.series_eta.setObjectName("hudExplanation")
        self.series_eta.setWordWrap(False)
        self.series_eta.hide()
        set_accessible(self.series_eta, "Background AI time estimate")
        layout.addWidget(self.series_eta)

        self.suggestion_count = QLabel("0 sentences", page)
        self.suggestion_count.hide()
        self.scope_label = QLabel("Sentence selection", page)
        self.scope_label.hide()

        self.suggestion_title = QLabel("No suggestion selected")
        self.suggestion_title.setObjectName("hudSuggestionTitle")
        self.suggestion_title.setWordWrap(True)
        self.suggestion_title.setMaximumHeight(32)
        self.suggestion_title.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        layout.addWidget(self.suggestion_title)

        self.suggestion_explanation = QLabel(
            "Select text to see why a correction is suggested."
        )
        self.suggestion_explanation.setObjectName("hudExplanation")
        self.suggestion_explanation.setWordWrap(True)
        self.suggestion_explanation.setMaximumHeight(46)
        self.suggestion_explanation.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        layout.addWidget(self.suggestion_explanation)

        comparison = QGridLayout()
        comparison.setHorizontalSpacing(6)
        comparison.setVerticalSpacing(3)
        self.before_heading = QLabel("Original sentence")
        self.before_heading.setObjectName("hudEyebrow")
        self.before_text = QPlainTextEdit("Not available")
        self.before_text.setObjectName("hudTextBox")
        self.before_text.setReadOnly(True)
        self.before_text.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.before_text.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.before_text.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.before_text.setMinimumHeight(64)
        self.before_text.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.after_heading = QLabel("Suggested text")
        self.after_heading.setObjectName("hudEyebrow")
        self.after_heading.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        comparison.addWidget(self.before_heading, 0, 0)
        comparison.addWidget(self.before_text, 1, 0)
        comparison.addWidget(self.after_heading, 2, 0)
        self.after_text = QTextEdit("Not available")
        self.after_text.setObjectName("hudTextBoxAccent")
        self.after_text.setAccessibleName("Suggested correction with highlighted changes")
        self.after_text.setReadOnly(True)
        self.after_text.setAcceptRichText(False)
        self.after_text.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.after_text.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.after_text.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.after_text.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.after_text.setMinimumHeight(64)
        self.after_text.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        comparison.addWidget(self.after_text, 3, 0)
        comparison.setColumnStretch(0, 1)
        comparison.setRowStretch(1, 1)
        comparison.setRowStretch(3, 1)
        layout.addLayout(comparison, 2)

        self.queue_heading = QLabel("Next replacements")
        self.queue_heading.setObjectName("hudEyebrow")
        layout.addWidget(self.queue_heading)
        self.queue_preview = QPlainTextEdit()
        self.queue_preview.setObjectName("hudQueue")
        self.queue_preview.setReadOnly(True)
        self.queue_preview.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.queue_preview.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.queue_preview.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.queue_preview.setMinimumHeight(64)
        self.queue_preview.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout.addWidget(self.queue_preview, 1)

        tone_row = QHBoxLayout()
        tone_label = QLabel("Tone")
        tone_label.setObjectName("hudEyebrow")
        tone_row.addWidget(tone_label)
        self.tone_combo = QComboBox()
        self.tone_combo.addItems(
            (
                "Clear",
                "Improve",
                "Shorten",
                "Friendly",
                "Formal",
                "Academic",
                "Simplified",
            )
        )
        self.tone_combo.setAccessibleName("Rewrite tone")
        self.tone_combo.setFixedHeight(32)
        self.tone_combo.currentTextChanged.connect(self._tone_selected)
        tone_row.addWidget(self.tone_combo, 1)
        self.rewrite_sentence_button = QPushButton("Regenerate")
        self.rewrite_sentence_button.setAccessibleName("Rewrite current text locally")
        self.rewrite_sentence_button.setFixedHeight(32)
        self.rewrite_sentence_button.clicked.connect(self._request_sentence_rewrite)
        tone_row.addWidget(self.rewrite_sentence_button, 1)
        layout.addLayout(tone_row)

        actions = QHBoxLayout()
        self.apply_suggestion_button = QPushButton("Replace Text")
        self.apply_suggestion_button.setAccessibleName("Replace text in source box")
        self.apply_suggestion_button.setObjectName("primaryButton")
        self.apply_suggestion_button.setFixedHeight(32)
        self.apply_suggestion_button.clicked.connect(self._apply_current_suggestion)
        actions.addWidget(self.apply_suggestion_button, 1)
        self.dismiss_suggestion_button = QPushButton("Dismiss")
        self.dismiss_suggestion_button.setAccessibleName("Dismiss suggested text")
        self.dismiss_suggestion_button.setObjectName("hudTextButton")
        self.dismiss_suggestion_button.clicked.connect(self._dismiss_current_suggestion)
        self.dismiss_suggestion_button.setFixedHeight(32)
        actions.addWidget(self.dismiss_suggestion_button, 1)
        layout.addLayout(actions)

        self.previous_button = QPushButton("Previous", page)
        self.previous_button.hide()
        self.next_button = QPushButton("Next", page)
        self.next_button.hide()


        self.apply_changes_button = QPushButton("Apply changes", page)
        self.apply_changes_button.setObjectName("primaryButton")
        self.apply_changes_button.setEnabled(False)
        self.apply_changes_button.clicked.connect(self._replace)
        self.apply_changes_button.hide()
        self.document_button = QPushButton("Document", page)
        self.document_button.clicked.connect(self.document_requested.emit)
        self.document_button.hide()
        self.assistant_button = QPushButton("More", page)
        self.assistant_button.clicked.connect(lambda: self._set_view("assistant", activate=True))
        self.assistant_button.hide()

        self.status = QLabel("Waiting for selected text.")
        self.status.setObjectName("hudStatus")
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(20)
        self.status.setMaximumHeight(44)
        layout.addWidget(self.status)
        return page

    def _build_assistant_page(self) -> QWidget:
        page = QFrame()
        page.setObjectName("hudAssistant")
        root = QVBoxLayout(page)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(9)

        header = QGridLayout()
        header.setHorizontalSpacing(8)
        header.setVerticalSpacing(4)
        self.back_button = QPushButton("Back")
        self.back_button.setObjectName("hudQuietButton")
        self.back_button.clicked.connect(self._back_from_assistant)
        header.addWidget(self.back_button, 0, 0, 2, 1)
        title = QLabel("Rewrite assistant")
        title.setObjectName("hudTitle")
        header.addWidget(title, 0, 1)
        privacy = QLabel("LOCAL ONLY")
        privacy.setObjectName("privacyBadge")
        header.addWidget(privacy, 1, 1, Qt.AlignmentFlag.AlignLeft)
        self.assistant_close_button = QPushButton("Close")
        self.assistant_close_button.setObjectName("hudQuietButton")
        self.assistant_close_button.clicked.connect(self.reject)
        header.addWidget(self.assistant_close_button, 0, 2, 2, 1)
        header.setColumnStretch(1, 1)
        root.addLayout(header)

        self.content_scroll = QScrollArea()
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(2, 2, 6, 2)
        layout.setSpacing(9)

        original_band = QFrame()
        original_band.setObjectName("entryBand")
        original_layout = QVBoxLayout(original_band)
        original_layout.setContentsMargins(10, 7, 10, 9)
        original_layout.setSpacing(5)
        original_header = QHBoxLayout()
        original_label = QLabel("Captured text")
        original_label.setObjectName("fieldHeading")
        original_header.addWidget(original_label)
        original_header.addStretch(1)
        self.assistant_document_button = QPushButton("Check entire document")
        self.assistant_document_button.clicked.connect(self.document_requested.emit)
        original_header.addWidget(self.assistant_document_button)
        original_layout.addLayout(original_header)
        self.assistant_scope_label = QLabel("Sentence selection")
        self.assistant_scope_label.setObjectName("mutedLabel")
        original_layout.addWidget(self.assistant_scope_label)
        self.source_edit = QPlainTextEdit()
        self.source_edit.setReadOnly(True)
        self.source_edit.setMaximumHeight(150)
        self.source_edit.setPlaceholderText("Captured text appears here.")
        original_label.setBuddy(self.source_edit)
        original_layout.addWidget(self.source_edit)
        layout.addWidget(original_band)

        suggestion_header = QHBoxLayout()
        suggestion_title = QLabel("Writing suggestions")
        suggestion_title.setObjectName("fieldHeading")
        suggestion_header.addWidget(suggestion_title)
        suggestion_header.addStretch(1)
        self.assistant_suggestion_count = QLabel("0 suggestions")
        self.assistant_suggestion_count.setObjectName("mutedLabel")
        suggestion_header.addWidget(self.assistant_suggestion_count)
        layout.addLayout(suggestion_header)

        filter_row = QGridLayout()
        filter_row.setHorizontalSpacing(6)
        filter_row.setVerticalSpacing(6)
        self.filter_group = QButtonGroup(self)
        self.filter_group.setExclusive(True)
        self.filter_buttons: dict[str, QPushButton] = {}
        for index, name in enumerate(FILTERS):
            button = QPushButton(name)
            button.setCheckable(True)
            button.setProperty("hudFilter", True)
            button.setAccessibleName(f"Show {name.lower()} writing suggestions")
            button.clicked.connect(lambda _checked=False, value=name: self._set_filter(value))
            self.filter_group.addButton(button)
            self.filter_buttons[name] = button
            filter_row.addWidget(button, index // 3, index % 3)
        self.filter_buttons["All"].setChecked(True)
        layout.addLayout(filter_row)

        self.suggestion_list = QListWidget()
        self.suggestion_list.setMinimumHeight(86)
        self.suggestion_list.setMaximumHeight(120)
        self.suggestion_list.currentRowChanged.connect(self._suggestion_selected)
        layout.addWidget(self.suggestion_list)

        suggestion_actions = QHBoxLayout()
        self.assistant_apply_button = QPushButton("Accept selected sentence")
        self.assistant_apply_button.clicked.connect(self._apply_assistant_suggestion)
        suggestion_actions.addWidget(self.assistant_apply_button)
        self.apply_all_button = QPushButton("Accept all sentences")
        self.apply_all_button.clicked.connect(self._apply_all)
        suggestion_actions.addWidget(self.apply_all_button)
        suggestion_actions.addStretch(1)
        layout.addLayout(suggestion_actions)

        action_title = QLabel("Rewrite and style")
        action_title.setObjectName("fieldHeading")
        layout.addWidget(action_title)
        action_grid = QGridLayout()
        action_grid.setHorizontalSpacing(7)
        action_grid.setVerticalSpacing(7)
        self.command_buttons: dict[str, QPushButton] = {}
        for index, command in enumerate(COMMANDS):
            button = QPushButton(command)
            button.setProperty("segment", True)
            button.setToolTip(COMMANDS[command])
            button.setAccessibleName(f"{command} captured text")
            button.clicked.connect(
                lambda _checked=False, value=command: self._request_command(value)
            )
            action_grid.addWidget(button, index // 2, index % 2)
            self.command_buttons[command] = button
        layout.addLayout(action_grid)

        self.language_edit = QLineEdit()
        self.language_edit.setPlaceholderText("Translation language, only needed for Translate")
        layout.addWidget(self.language_edit)

        result_label = QLabel("Editable result preview")
        result_label.setObjectName("fieldHeading")
        self.result_edit = QPlainTextEdit()
        self.result_edit.setPlaceholderText(
            "Accept a suggestion or run a rewrite command. Review the result here."
        )
        self.result_edit.setMinimumHeight(150)
        self.result_edit.setMaximumHeight(240)
        self.result_edit.textChanged.connect(self._result_edited)
        result_label.setBuddy(self.result_edit)
        layout.addWidget(result_label)
        layout.addWidget(self.result_edit)
        layout.addStretch(1)
        self.content_scroll.setWidget(content)
        root.addWidget(self.content_scroll, 1)

        decision_row = QGridLayout()
        decision_row.setHorizontalSpacing(8)
        decision_row.setVerticalSpacing(7)
        self.replace_button = QPushButton("Apply changes to selection")
        self.replace_button.setObjectName("primaryButton")
        self.replace_button.clicked.connect(self._replace)
        decision_row.addWidget(self.replace_button, 0, 0, 1, 2)
        self.stage_button = QPushButton("Stage private paste")
        self.stage_button.clicked.connect(self._stage)
        decision_row.addWidget(self.stage_button, 1, 0)
        self.reject_button = QPushButton("Reject")
        self.reject_button.setObjectName("dangerButton")
        self.reject_button.clicked.connect(self.reject)
        decision_row.addWidget(self.reject_button, 1, 1)
        root.addLayout(decision_row)

        self.assistant_status = QLabel("Waiting for selected text.")
        self.assistant_status.setObjectName("hudStatus")
        self.assistant_status.setWordWrap(True)
        root.addWidget(self.assistant_status)
        return page

    def _configure_accessibility(self) -> None:
        set_accessible(self.badge_button, "Manuscript writing status")
        set_accessible(self.loading_close_button, "Close Writing HUD while proofreading")
        set_accessible(self.settings_button, "Open Manuscript settings")
        set_accessible(self.close_button, "Close Writing HUD without changing text")
        set_accessible(self.assistant_close_button, "Close rewrite assistant without changing text")
        set_accessible(self.card_position, "Current writing suggestion position")
        set_accessible(self.suggestion_count, "Local writing suggestion count")
        set_accessible(self.before_text, "Suggestion original text")
        set_accessible(self.after_text, "Suggestion replacement text")
        set_accessible(self.queue_preview, "Upcoming writing replacements")
        set_accessible(self.suggestion_explanation, "Suggestion explanation")
        set_accessible(self.rewrite_sentence_button, "Rewrite the current sentence locally")
        set_accessible(self.tone_combo, "Sentence rewrite tone")
        set_accessible(self.apply_suggestion_button, "Replace text in source box")
        set_accessible(self.dismiss_suggestion_button, "Dismiss current sentence")
        set_accessible(self.assistant_button, "Open the full local rewrite assistant")
        set_accessible(self.apply_changes_button, "Apply accepted changes to the source text")
        set_accessible(self.document_button, "Check the entire focused document")
        set_accessible(self.assistant_document_button, "Check the entire focused document")
        set_accessible(self.back_button, "Return to the compact suggestion card")
        set_accessible(self.source_edit, "Captured original text")
        set_accessible(self.assistant_suggestion_count, "Assistant suggestion count")
        set_accessible(self.suggestion_list, "Local writing suggestions")
        set_accessible(self.assistant_apply_button, "Accept selected sentence into preview")
        set_accessible(self.apply_all_button, "Accept all suggested sentences into preview")
        set_accessible(self.language_edit, "Translation language")
        set_accessible(self.result_edit, "Editable local writing result preview")
        set_accessible(
            self.replace_button,
            "Replace captured selection",
            "Explicitly replaces the still-matching selection with the reviewed preview.",
        )
        set_accessible(
            self.stage_button,
            "Stage preview for private paste",
            "Stores the reviewed preview in Manuscript memory without the Windows clipboard.",
        )
        set_accessible(self.reject_button, "Reject writing HUD result")
        set_accessible(self.status, "Writing HUD status", self.status.text())
        set_accessible(self.assistant_status, "Writing assistant status", self.assistant_status.text())

    def show_capture(
        self,
        text: str,
        *,
        automatic: bool = False,
        anchor: tuple[int, int, int, int] | None = None,
        document_scope: bool = False,
    ) -> None:
        self._target_marking_enabled = False
        self.target_changed.emit(-1, -1)
        self._analysis_timer.stop()
        self._no_issues_timer.stop()
        self._pulse_timer.stop()
        self._ai_timeout_timer.stop()
        self._ai_progress_timer.stop()
        self._active_ai_attempt_id = 0
        self._active_ai_suggestion_id = ""
        self._closing_after_choice = False
        self._automatic = bool(automatic)
        self._user_opened = False
        self._document_scope = bool(document_scope)
        self._anchor = anchor
        self._user_positioned = False
        self._dismissed_ids.clear()
        self._ai_requested_ids.clear()
        self._ai_completed_ids.clear()
        self._ai_request_started.clear()
        self._ai_duration_samples.clear()
        self._ai_failed_ids.clear()
        self._review_resolved.clear()
        self._review_resolved.clear()
        self._ai_auto_retry_counts.clear()
        self._ai_ready_messages.clear()
        self._ai_failure_messages.clear()
        self._review_index = 0
        self._review_progress_total = 0
        self._review_progress_completed = 0
        self._accepted_external_changes = False
        self.source_edit.setPlainText(text)
        normalized_text = self.source_edit.toPlainText()
        self._set_result_text("")
        self._preview_base = normalized_text
        self.language_edit.clear()
        self.filter_buttons["All"].setChecked(True)
        self.set_busy(False)
        self._update_scope_controls()

        if automatic:
            self._suggestions = ()
            self._analysis_pending = True
            self._pulse_step = 0
            self._set_badge_state("...")
            self._set_status("Selected text detected. Analyzing locally.")
            self._set_view("badge", activate=False, reposition=False)
            self._show_near_pointer(activate=False, anchor=anchor)
            self._pulse_timer.start()
            self._analysis_timer.start()
            return

        self._analysis_pending = False
        self._analyze_suggestions(normalized_text)
        captured = "Entire document loaded." if self._document_scope else "Selected text captured."
        self._set_status(self._suggestion_status(captured))
        self._set_view("badge", activate=False, reposition=False)
        self._show_near_pointer(activate=False, anchor=anchor)

    def _finish_automatic_analysis(self) -> None:
        if not self._analysis_pending or not self.source_edit.toPlainText().strip():
            return
        self._analysis_pending = False
        self._pulse_timer.stop()
        self._analyze_suggestions(self.source_edit.toPlainText())
        self._set_status(
            self._suggestion_status("Sentence ready. Click the Manuscript icon.")
        )
        self._update_badge()
        self._set_view("badge", activate=False)
        if self.isVisible():
            self._keep_target_active()

    def _pulse_badge(self) -> None:
        if not self._analysis_pending:
            self._pulse_timer.stop()
            return
        frames = (".", "..", "...")
        self._set_badge_state(frames[self._pulse_step % len(frames)])
        self._pulse_step += 1

    def _set_badge_state(self, text: str) -> None:
        self.badge_button.setText("")
        self.badge_button.setToolTip("Analyzing selected text locally.")
        self.badge_button.setAccessibleName("Manuscript is analyzing selected text locally")

    def _update_badge(self) -> None:
        total = len(self._review_suggestions)
        self.badge_button.setText("")
        label = f"{total} review" + ("" if total == 1 else "s")
        tooltip = f"{label} ready. Click for writing suggestions."
        self.badge_button.setToolTip(tooltip)
        self.badge_button.setAccessibleName(f"Manuscript, {tooltip}")

    def _open_from_badge(self) -> None:
        if self._badge_dragged:
            # The click that ends a drag must not also open the card.
            self._badge_dragged = False
            return
        if self._analysis_pending:
            self._analysis_timer.stop()
            self._finish_automatic_analysis()
        self._user_opened = True
        self._target_marking_enabled = True
        if self._ai_proofread:
            # The review card was populated by fast local rules while it was
            # hidden behind the badge. Clear that placeholder before any AI
            # loading view can reveal it as if it were the finished answer.
            self.after_text.clear()
            self.after_text.setPlaceholderText("AI correction is being prepared...")
            self.after_text.setToolTip("AI correction is being prepared.")
            self._set_view("loading", activate=True)
            if self._request_ai_proofread_current():
                return
        self._set_view("card", activate=True)
        self.apply_suggestion_button.setFocus(Qt.FocusReason.MouseFocusReason)

    def open_current_capture(self) -> None:
        """Open the already captured text without rereading the host application."""
        self._open_from_badge()

    def _back_from_assistant(self) -> None:
        self._set_view("card" if self._review_suggestions else "badge", activate=True)

    def _set_view(
        self,
        view: str,
        *,
        activate: bool,
        reposition: bool = True,
    ) -> None:
        pages = {
            "badge": self.badge_page,
            "loading": self.loading_page,
            "card": self.card_page,
            "assistant": self.assistant_page,
        }
        if view not in pages:
            raise ValueError(f"Unknown HUD view: {view}")
        old_position = self.pos()
        self._view = view
        if view == "badge":
            self.target_changed.emit(-1, -1)
        target_page = pages[view]
        current_page = self.stack.currentWidget()
        if current_page is not target_page:
            if current_page is not None:
                self.stack.removeWidget(current_page)
                current_page.hide()
            self.stack.addWidget(target_page)
        self.stack.setCurrentWidget(target_page)
        target_page.show()
        self._apply_view_size()
        if self.isVisible():
            self.show()
            # Windows can expand a visible frameless Qt window to the newly
            # revealed page's size hint. Reassert the intended disclosure size.
            self._apply_view_size()
            if reposition and not self._user_positioned:
                self._position_on_screen(anchor=self._anchor)
            else:
                self.move(old_position)
                self._clamp_current_position()
            if activate:
                self.raise_()
                self.activateWindow()
            else:
                self._keep_target_active()
            QTimer.singleShot(50, self._reassert_current_view_size)
        self._publish_current_target()

    def _apply_view_size(self) -> None:
        screen = QApplication.screenAt(self.pos()) or QApplication.primaryScreen()
        area = screen.availableGeometry() if screen is not None else None
        maximum_width = max(1, area.width() - 8) if area is not None else None
        maximum_height = max(1, area.height() - 8) if area is not None else None
        requested = {
            "badge": lambda: self._BADGE_SIZE,
            "loading": lambda: self._LOADING_SIZE,
            "card": lambda: self._desired_card_size(
                maximum_width=maximum_width,
                maximum_height=maximum_height,
            ),
            "assistant": lambda: self._ASSISTANT_SIZE,
        }[self._view]()
        if self._view in {"card", "assistant"}:
            base_minimum = (
                QSize(480, 320)
                if self._view == "card"
                else QSize(480, 400)
            )
            if area is None:
                maximum = QSize(16777215, 16777215)
            else:
                maximum = QSize(maximum_width, maximum_height)
            minimum = QSize(
                min(base_minimum.width(), maximum.width()),
                min(base_minimum.height(), maximum.height()),
            )
            self.setMinimumSize(minimum)
            self.setMaximumSize(maximum)
            target = self._user_view_sizes.get(self._view, requested)
            target = QSize(
                max(minimum.width(), min(target.width(), maximum.width())),
                max(minimum.height(), min(target.height(), maximum.height())),
            )
            self._programmatic_resize = True
            try:
                self.resize(target)
            finally:
                self._programmatic_resize = False
            self._resize_grip.show()
            self._resize_grip.raise_()
            self._position_resize_grip()
            return
        self._resize_grip.hide()
        if screen is None:
            self.setFixedSize(requested)
            self.resize(requested)
            return
        width = max(1, min(requested.width(), area.width() - 8))
        height = max(1, min(requested.height(), area.height() - 8))
        self.setFixedSize(width, height)
        self.resize(width, height)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if (
            not self._programmatic_resize
            and event.spontaneous()
            and self._view in {"card", "assistant"}
            and self.isVisible()
        ):
            self._user_view_sizes[self._view] = QSize(event.size())
        if hasattr(self, "_resize_grip"):
            self._position_resize_grip()

    def _position_resize_grip(self) -> None:
        hint = self._resize_grip.sizeHint()
        self._resize_grip.setGeometry(
            max(0, self.width() - hint.width()),
            max(0, self.height() - hint.height()),
            hint.width(),
            hint.height(),
        )

    def _desired_card_size(
        self,
        *,
        maximum_width: int | None = None,
        maximum_height: int | None = None,
    ) -> QSize:
        text = self.after_text.toPlainText().strip() if hasattr(self, "after_text") else ""
        metrics = QFontMetrics(self.after_text.font())
        natural_width = metrics.horizontalAdvance(" ".join(text.splitlines()) or " ")
        overflow_width = max(0, natural_width - self._CARD_SIZE.width())
        # Grow both axes continuously. A square-root curve prevents a paragraph
        # from becoming one screen-wide line before the card gains height.
        desired_width = self._CARD_SIZE.width() + round(math.sqrt(overflow_width) * 4)
        width_limit = self._CARD_MAX_WIDTH
        if maximum_width is not None:
            width_limit = min(width_limit, maximum_width)
        width = min(desired_width, width_limit)
        # QTextEdit loses space to its frame, padding, and optional scroll bar.
        # Measure against the real usable width so the card grows before text clips.
        text_width = max(120, width - 58)
        bounds = metrics.boundingRect(
            QRect(0, 0, text_width, 1_000_000),
            Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft,
            text or " ",
        )
        text_height = max(64, bounds.height() + 24)
        if hasattr(self, "after_text"):
            self.after_text.setMinimumHeight(min(text_height, 220))
            self.after_text.updateGeometry()
        desired_height = self._CARD_SIZE.height() + max(
            0, min(text_height, 220) - 64
        )
        height_limit = self._CARD_MAX_HEIGHT
        if maximum_height is not None:
            height_limit = min(height_limit, maximum_height)
        height = min(desired_height, height_limit)
        return QSize(width, height)

    def set_busy(self, busy: bool, command: str = "") -> None:
        self._busy = bool(busy)
        for button in self.command_buttons.values():
            button.setEnabled(not busy)
        self.language_edit.setEnabled(not busy)
        self.apply_suggestion_button.setEnabled(
            not busy and bool(self._review_suggestions)
        )
        self.rewrite_sentence_button.setEnabled(
            not busy and bool(self._review_suggestions)
        )
        self.tone_combo.setEnabled(not busy and bool(self._review_suggestions))
        self.dismiss_suggestion_button.setEnabled(
            not busy and bool(self._review_suggestions)
        )
        self.assistant_apply_button.setEnabled(
            not busy and self.suggestion_list.currentRow() >= 0
        )
        self.apply_all_button.setEnabled(
            not busy and bool(self._available_suggestions())
        )
        if busy:
            message = f"Running {command} locally..."
            if command == "AI proofread":
                message = "Proofreading locally..."
            self.loading_status.setText(message)
            self._set_status(message)
            self.apply_suggestion_button.setToolTip(message)
            self.apply_suggestion_button.setAccessibleName(
                f"Replace text in source box. {message}"
            )
            self.apply_suggestion_button.setAccessibleDescription(message)
        self._refresh_decision_buttons()

    def show_result(self, text: str, message: str) -> None:
        message = user_ai_status(message)
        self.set_busy(False)
        self._preview_base = text
        self._set_result_text(text)
        self._dismissed_ids.clear()
        self._analyze_suggestions(text)
        self._set_status(message)
        self._set_view("assistant", activate=True)
        self.result_edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def show_sentence_result(
        self,
        attempt_id: int,
        suggestion_id: str,
        text: str,
        message: str,
    ) -> None:
        message = user_ai_status(message)
        attempt = int(attempt_id)
        if attempt not in self._ai_attempts and attempt != self._active_ai_attempt_id:
            logger.info(
                "Writing HUD ignored stale AI result attempt=%s active=%s suggestion=%s",
                attempt_id,
                self._active_ai_attempt_id,
                suggestion_id,
            )
            return
        self._ai_attempts.pop(attempt, None)
        foreground = attempt == self._active_ai_attempt_id
        was_background = self._active_ai_background if foreground else True
        if foreground:
            self._ai_timeout_timer.stop()
            self._ai_progress_timer.stop()
            self._active_ai_attempt_id = 0
            self._active_ai_suggestion_id = ""
            self._active_ai_background = False
            self.set_busy(False)
        rewritten = text
        if not rewritten.strip():
            self._restore_visible_suggestion(suggestion_id)
            self.show_error(
                "Sentence rewrite returned no text. Showing the current correction."
            )
            return
        updated_suggestions = []
        found = False
        for suggestion in self._suggestions:
            if str(getattr(suggestion, "suggestion_id", "")) != suggestion_id:
                updated_suggestions.append(suggestion)
                continue
            found = True
            updated_suggestions.append(
                replace(
                    suggestion,
                    category="Rewrite",
                    categories=("Rewrite",),
                    title="Local sentence rewrite",
                    explanation="Review this local rewrite, then choose Replace Text or Dismiss.",
                    replacement=rewritten,
                    has_changes=rewritten != str(suggestion.original),
                )
            )
        if not found:
            self._set_status("That sentence changed. Rewrite it again.", assertive=True)
            return
        self._suggestions = tuple(updated_suggestions)
        self._ai_completed_ids.add(suggestion_id)
        started = self._ai_request_started.pop(suggestion_id, 0.0)
        if started > 0:
            self._ai_duration_samples.append(
                max(0.1, time.monotonic() - started)
            )
            del self._ai_duration_samples[:-8]
        self._ai_failed_ids.discard(suggestion_id)
        self._ai_failure_messages.pop(suggestion_id, None)
        self._ai_ready_messages[suggestion_id] = message
        self._update_series_progress()
        self._render_suggestions()
        QTimer.singleShot(0, self._reassert_current_view_size)
        current = self._current_review_suggestion()
        current_id = str(getattr(current, "suggestion_id", ""))
        current_message = self._ai_ready_messages.get(current_id, message)
        self._set_status(current_message if was_background else message)
        if self._view == "loading":
            self._set_view("card", activate=True)
            self.apply_suggestion_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self.apply_suggestion_button.setToolTip(current_message)
        self.apply_suggestion_button.setAccessibleName(
            f"Replace text in source box. {current_message}"
        )
        self.apply_suggestion_button.setAccessibleDescription(current_message)
        QTimer.singleShot(0, self._request_next_ai_prefetch)

    def show_sentence_error(
        self,
        attempt_id: int,
        suggestion_id: str,
        message: str,
    ) -> None:
        message = user_ai_status(message)
        attempt = int(attempt_id)
        if attempt not in self._ai_attempts and attempt != self._active_ai_attempt_id:
            logger.info(
                "Writing HUD ignored stale AI error attempt=%s active=%s suggestion=%s",
                attempt_id,
                self._active_ai_attempt_id,
                suggestion_id,
            )
            return
        self._ai_attempts.pop(attempt, None)
        foreground = attempt == self._active_ai_attempt_id
        was_background = self._active_ai_background if foreground else True
        if foreground:
            self._ai_timeout_timer.stop()
            self._ai_progress_timer.stop()
            self._active_ai_attempt_id = 0
            self._active_ai_suggestion_id = ""
            self._active_ai_background = False
            self.set_busy(False)
        self._ai_failed_ids.add(suggestion_id)
        self._ai_failure_messages[suggestion_id] = message
        self._render_suggestions()
        self._set_status(message, assertive=True)
        current = self._current_review_suggestion()
        current_id = str(getattr(current, "suggestion_id", ""))
        if (
            was_background
            and
            self._view == "card"
            and current_id
            and current_id != suggestion_id
            and current_id in self._ai_completed_ids
        ):
            self.apply_suggestion_button.setEnabled(True)
            self.apply_suggestion_button.setFocus(Qt.FocusReason.OtherFocusReason)
            QTimer.singleShot(0, self._request_next_ai_prefetch)
            return
        # Keep a failed section reviewable, but continue processing every
        # later section instead of retrying the same failed text forever.
        self._set_view("card", activate=True, reposition=False)
        self._show_review_suggestion()
        self.rewrite_sentence_button.setFocus(Qt.FocusReason.OtherFocusReason)
        QTimer.singleShot(0, self._request_next_ai_prefetch)

    def show_error(self, message: str) -> None:
        message = user_ai_status(message)
        self.set_busy(False)
        if not self.after_text.toPlainText().strip():
            self._restore_visible_suggestion()
        self._set_status(message, assertive=True)
        self.apply_suggestion_button.setToolTip(message)
        self.apply_suggestion_button.setAccessibleName(
            f"Replace text in source box. {message}"
        )
        self.apply_suggestion_button.setAccessibleDescription(message)
        if self._view == "loading":
            self._set_view("card", activate=True, reposition=False)
            self.apply_suggestion_button.setFocus(Qt.FocusReason.OtherFocusReason)
        elif not self.isVisible():
            self._set_view("card", activate=True, reposition=False)
            self._show_near_pointer(activate=True, anchor=self._anchor)

    def require_recapture(self, message: str) -> None:
        """Keep captured text visible but invalidate every stale result action."""
        self._analysis_timer.stop()
        self._pulse_timer.stop()
        self._ai_timeout_timer.stop()
        self._ai_progress_timer.stop()
        self._active_ai_attempt_id = 0
        self._active_ai_suggestion_id = ""
        self._ai_attempts.clear()
        self._analysis_pending = False
        self._busy = False
        self._preview_base = ""
        self._suggestions = ()
        self._dismissed_ids.clear()
        self._set_result_text("")
        self._render_suggestions()
        self.suggestion_title.setText("Fresh selection required")
        self.suggestion_explanation.setText(message)
        for button in self.command_buttons.values():
            button.setEnabled(False)
        self.language_edit.setEnabled(False)
        self.apply_suggestion_button.setEnabled(False)
        self.rewrite_sentence_button.setEnabled(False)
        self.tone_combo.setEnabled(False)
        self.dismiss_suggestion_button.setEnabled(False)
        self.assistant_apply_button.setEnabled(False)
        self.apply_all_button.setEnabled(False)
        self.apply_changes_button.setEnabled(False)
        self.document_button.setEnabled(False)
        self.assistant_document_button.setEnabled(False)
        self.replace_button.setEnabled(False)
        self.stage_button.setEnabled(False)
        self._set_status(message, assertive=True)
        self._set_view("card", activate=True, reposition=not self.isVisible())
        if not self.isVisible():
            self._show_near_pointer(activate=True, anchor=self._anchor)

    def _collapse_after_no_issues(self) -> None:
        # Guard against the world moving under the timer: anything that gave
        # the writer something to read or act on cancels the collapse.
        if not self.isVisible() or self._busy:
            return
        if self._review_suggestions:
            return
        self.finish_choice()

    def finish_choice(self) -> None:
        self._target_marking_enabled = False
        self._user_opened = False
        self._no_issues_timer.stop()
        self.target_changed.emit(-1, -1)
        self._analysis_timer.stop()
        self._pulse_timer.stop()
        self._ai_timeout_timer.stop()
        self._ai_progress_timer.stop()
        self._active_ai_attempt_id = 0
        self._active_ai_suggestion_id = ""
        self._ai_attempts.clear()
        self._analysis_pending = False
        self._closing_after_choice = True
        self.hide()
        self.source_edit.clear()
        self._set_result_text("")
        self._preview_base = ""
        self._document_scope = False
        self._suggestions = ()
        self._dismissed_ids.clear()
        self._ai_requested_ids.clear()
        self._ai_completed_ids.clear()
        self._ai_request_started.clear()
        self._ai_duration_samples.clear()
        self._ai_failed_ids.clear()
        self._review_resolved.clear()
        self._review_resolved.clear()
        self._ai_auto_retry_counts.clear()
        self._ai_ready_messages.clear()
        self._ai_failure_messages.clear()
        self._render_suggestions()

    def _request_command(self, command: str) -> None:
        if self._busy or not self.source_edit.toPlainText().strip():
            return
        language = self.language_edit.text().strip()
        if command == "Translate" and not language:
            self._set_status("Enter a translation language first.", assertive=True)
            self.language_edit.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        self.set_busy(True, command)
        self.command_requested.emit(command, language)

    def _request_sentence_rewrite(self) -> None:
        suggestion = self._current_review_suggestion()
        if self._busy or suggestion is None:
            return
        source = str(getattr(suggestion, "replacement", "")).strip()
        if not source:
            return
        suggestion_id = str(getattr(suggestion, "suggestion_id", ""))
        self._cancel_active_ai_request()
        self._ai_requested_ids.discard(suggestion_id)
        self._ai_failed_ids.discard(suggestion_id)
        self._ai_failure_messages.pop(suggestion_id, None)
        self._set_view("loading", activate=True)
        self._start_sentence_ai_request(
            suggestion_id,
            source,
            self._selected_tone_command(),
            "sentence rewrite",
        )

    def _selected_tone_command(self, *, automatic: bool = False) -> str:
        command = {
            "Clear": "Rewrite",
            "Improve": "Improve",
            "Rephrase": "Rephrase",
            "Shorten": "Shorten",
            "Friendly": "Friendly",
            "Formal": "Formal",
            "Professional": "Professional",
            "Academic": "Academic",
            "Conversational": "Conversational",
            "Concise": "Concise",
            "Detailed": "Detailed",
            "Persuasive": "Persuasive",
            "Confident": "Confident",
            "Casual": "Casual",
            "Technical": "Technical",
            "Simplified": "Simplified",
            "High-school level": "High-school level",
            "College level": "College level",
            "Diplomatic": "Diplomatic",
            "Empathetic": "Empathetic",
            "Cooperative": "Cooperative",
            "Assertive": "Assertive",
            "Caveman": "Caveman",
        }.get(self.tone_combo.currentText(), "Rewrite")
        return "Proofread" if automatic and command == "Rewrite" else command

    def _tone_selected(self, _tone: str) -> None:
        suggestion = self._current_review_suggestion()
        if suggestion is None or self._view not in {"card", "loading"}:
            return
        self._cancel_active_ai_request()
        self._request_sentence_rewrite()

    def _request_ai_proofread_current(self) -> bool:
        if not self._ai_proofread or self._busy or self._view not in {"card", "loading"}:
            return False
        suggestion = self._current_review_suggestion()
        if suggestion is None:
            return False
        suggestion_id = str(getattr(suggestion, "suggestion_id", ""))
        if (
            not suggestion_id
            or suggestion_id in self._ai_requested_ids
            or suggestion_id in self._ai_completed_ids
        ):
            return False
        source = str(getattr(suggestion, "replacement", "")).strip()
        if not source:
            return False
        self._ai_requested_ids.add(suggestion_id)
        self._start_sentence_ai_request(
            suggestion_id,
            source,
            self._selected_tone_command(automatic=True),
            "AI proofread" if self.tone_combo.currentText() == "Clear" else "tone rewrite",
        )
        return True

    def _request_next_ai_prefetch(self) -> bool:
        """Proofread remaining document sections without waiting for a decision.

        Runs up to the hardware-scaled width of attempts concurrently; each
        completion tops the pool back up, so on capable machines later
        sections generate while earlier ones are still being reviewed.
        """
        if (
            not self._ai_proofread
            or len(self._ai_attempts) >= self._ai_parallel_width()
            or self._view not in {"card", "loading"}
        ):
            return False
        for suggestion in self._review_suggestions:
            suggestion_id = str(getattr(suggestion, "suggestion_id", ""))
            if (
                not suggestion_id
                or suggestion_id in self._ai_requested_ids
                or suggestion_id in self._ai_completed_ids
                or suggestion_id in self._ai_failed_ids
            ):
                continue
            source = str(getattr(suggestion, "replacement", "")).strip()
            if not source:
                continue
            self._ai_requested_ids.add(suggestion_id)
            self._start_sentence_ai_request(
                suggestion_id,
                source,
                self._selected_tone_command(automatic=True),
                "AI proofread",
            )
            QTimer.singleShot(0, self._request_next_ai_prefetch)
            return True
        # Once the whole document has been attempted, automatically give each
        # failed section one fresh generation. This keeps later sections moving
        # while preventing a transient quality failure from blocking review.
        for suggestion in self._review_suggestions:
            suggestion_id = str(getattr(suggestion, "suggestion_id", ""))
            if (
                not suggestion_id
                or suggestion_id not in self._ai_failed_ids
                or self._ai_auto_retry_counts.get(suggestion_id, 0) >= 1
            ):
                continue
            source = str(getattr(suggestion, "replacement", "")).strip()
            if not source:
                continue
            self._ai_auto_retry_counts[suggestion_id] = (
                self._ai_auto_retry_counts.get(suggestion_id, 0) + 1
            )
            self._ai_requested_ids.discard(suggestion_id)
            self._ai_requested_ids.add(suggestion_id)
            self._start_sentence_ai_request(
                suggestion_id,
                source,
                self._selected_tone_command(automatic=True),
                "AI repair",
            )
            QTimer.singleShot(0, self._request_next_ai_prefetch)
            return True
        return False

    def _start_sentence_ai_request(
        self,
        suggestion_id: str,
        source: str,
        command: str,
        activity: str,
    ) -> None:
        # Windows editors separate paragraphs with bare \r, which language
        # models do not reliably read as a line break: they merged the
        # paragraphs of a multi-paragraph section into one. Send the model
        # standard \n breaks so the paragraph structure survives.
        source = source.replace("\r\n", "\n").replace("\r", "\n")
        self._ai_attempt_counter += 1
        attempt_id = self._ai_attempt_counter
        self._ai_attempts[attempt_id] = suggestion_id
        current = self._current_review_suggestion()
        current_id = str(getattr(current, "suggestion_id", ""))
        quiet = bool(
            self._view == "card"
            and (
                (
                    current_id
                    and current_id != suggestion_id
                    and (
                        current_id in self._ai_completed_ids
                        or current_id in self._ai_failed_ids
                    )
                )
                # An automatic retry of a section whose failure is already on
                # screen runs quietly: locking the controls for it trapped the
                # user on "AI could not finish this section".
                or suggestion_id in self._ai_failed_ids
            )
        )
        self._ai_request_started[suggestion_id] = time.monotonic()
        foreground_free = self._active_ai_attempt_id not in self._ai_attempts
        if foreground_free:
            self._active_ai_attempt_id = attempt_id
            self._active_ai_suggestion_id = suggestion_id
            self._active_ai_background = quiet
            self._ai_started_at = time.monotonic()
            self._ai_backend = "Checking GPU"
            self._ai_eta_seconds = None
            self._ai_timeout_timer.start()
            self._ai_progress_timer.start()
        else:
            # A parallel prefetch slot: it never owns the busy state, view,
            # or progress timers, and expires quietly on its own schedule.
            QTimer.singleShot(
                self._AI_TIMEOUT_MS,
                lambda attempt=attempt_id: self._expire_pool_attempt(attempt),
            )
        self._update_series_progress()
        if not foreground_free or (quiet and foreground_free):
            pass
        elif activity == "AI proofread":
            self.set_busy(True, activity)
            if self._view != "card" or not self._review_suggestions:
                self._set_view("loading", activate=True, reposition=False)
            message = "Checking GPU | 0s elapsed | ETA calibrating."
            self._set_status(message)
            self.apply_suggestion_button.setToolTip(message)
            self.apply_suggestion_button.setAccessibleDescription(message)
        else:
            self.set_busy(True, activity)
        self.sentence_rewrite_requested.emit(
            attempt_id,
            suggestion_id,
            source,
            command,
        )

    def _expire_pool_attempt(self, attempt_id: int) -> None:
        """Retire a background prefetch attempt that never came back."""
        if attempt_id == self._active_ai_attempt_id:
            return
        suggestion_id = self._ai_attempts.get(attempt_id, "")
        if not suggestion_id:
            return
        logger.info(
            "Writing HUD background AI attempt timed out attempt=%s suggestion=%s",
            attempt_id,
            suggestion_id,
        )
        self.show_sentence_error(
            attempt_id,
            suggestion_id,
            "AI took too long. This section needs retry.",
        )

    def show_sentence_progress(
        self,
        attempt_id: int,
        backend: str,
        eta_seconds: float | None,
    ) -> None:
        if int(attempt_id) != self._active_ai_attempt_id:
            return
        label = " ".join(str(backend).split())[:80]
        self._ai_backend = label or "Checking GPU"
        self._ai_eta_seconds = (
            float(eta_seconds)
            if eta_seconds is not None and float(eta_seconds) > 0
            else None
        )
        if not self._active_ai_background:
            self._update_ai_progress()

    def _series_progress_state(self) -> tuple[int, int, str]:
        """Return (ready, total, eta text) for the whole review series."""
        review_ids = [
            str(getattr(suggestion, "suggestion_id", ""))
            for suggestion in self._review_suggestions
        ]
        prepared = sum(
            1
            for suggestion_id in review_ids
            if suggestion_id and suggestion_id in self._ai_completed_ids
        )
        active_ids = set(self._ai_attempts.values())
        terminal_failed = sum(
            1
            for suggestion_id in review_ids
            if (
                suggestion_id
                and suggestion_id in self._ai_failed_ids
                and self._ai_auto_retry_counts.get(suggestion_id, 0) >= 1
                and suggestion_id not in active_ids
            )
        )
        ready = self._review_progress_completed + prepared
        progress = ready + terminal_failed
        total = max(
            self._review_progress_total,
            self._review_progress_completed + len(review_ids),
        )
        remaining = max(0, total - progress)
        if remaining <= 0:
            if terminal_failed:
                return (
                    progress,
                    total,
                    f"{ready} of {total} sections ready | {terminal_failed} needs retry.",
                )
            return progress, total, "All sections ready."
        if not self._ai_duration_samples:
            return progress, total, (
                f"Preparing {remaining} section"
                f"{'s' if remaining != 1 else ''} in the background..."
            )
        mean = sum(self._ai_duration_samples) / len(self._ai_duration_samples)
        eta = max(1, math.ceil(remaining * mean))
        retrying = bool(self._ai_auto_retry_counts) and remaining > 0
        note = (
            " One section needs extra attempts and may take longer."
            if retrying
            else ""
        )
        return progress, total, (
            f"{ready} of {total} sections ready | about {eta}s until all "
            f"sections are ready.{note}"
        )

    def _update_series_progress(self) -> None:
        ready, total, eta_text = self._series_progress_state()
        show = total > 1
        for bar in (self.series_progress, self.loading_progress):
            bar.setRange(0, max(1, total))
            bar.setValue(min(ready, total))
            bar.setVisible(show)
        for label in (self.series_eta, self.loading_eta):
            label.setText(eta_text if show else "")
            label.setVisible(show)
            label.setToolTip(eta_text)

    def _update_ai_progress(self) -> None:
        if not self._active_ai_attempt_id or self._ai_started_at <= 0:
            self._ai_progress_timer.stop()
            return
        elapsed = max(0, int(time.monotonic() - self._ai_started_at))
        if self._ai_eta_seconds is None:
            eta = "ETA calibrating"
        else:
            remaining = max(0, math.ceil(self._ai_eta_seconds - elapsed))
            eta = "finishing now" if remaining <= 0 else f"ETA about {remaining}s"
        message = f"{self._ai_backend} | {elapsed}s elapsed | {eta}."
        self._set_status(message)
        self.apply_suggestion_button.setToolTip(message)
        self.apply_suggestion_button.setAccessibleDescription(message)

    def _expire_ai_request(self) -> None:
        suggestion_id = self._active_ai_suggestion_id
        attempt_id = self._active_ai_attempt_id
        if not attempt_id or not suggestion_id:
            return
        logger.info(
            "Writing HUD AI request timed out attempt=%s suggestion=%s",
            attempt_id,
            suggestion_id,
        )
        self.show_sentence_error(
            attempt_id,
            suggestion_id,
            "AI took too long. This section needs retry.",
        )

    def _cancel_active_ai_request(self) -> None:
        suggestion_id = self._active_ai_suggestion_id
        self._ai_timeout_timer.stop()
        self._ai_progress_timer.stop()
        self._active_ai_attempt_id = 0
        self._active_ai_suggestion_id = ""
        self._active_ai_background = False
        for pending in self._ai_attempts.values():
            self._ai_requested_ids.discard(pending)
        self._ai_attempts.clear()
        if suggestion_id:
            self._ai_requested_ids.discard(suggestion_id)
        if self._busy:
            self.set_busy(False)

    def _analyze_suggestions(self, text: str) -> None:
        self._review_progress_completed = 0
        candidate = text
        if not candidate.strip():
            self._suggestions = ()
            self._review_progress_total = 0
            self._render_suggestions()
            return
        try:
            atomic = tuple(self._suggestion_engine.analyze(text))
            atomic = self._with_dictionary_spelling(text, atomic)
            self._suggestions = _group_sentence_suggestions(text, atomic)
            self._review_progress_total = len(self._suggestions)
            self._logged_progress = ""
            # The counter counts grouped sections, not paragraphs. When the
            # two differ a reader sees "8" where the document has 10, which
            # looks like sections went missing. Record both.
            logger.info(
                "Writing HUD review prepared sections=%d atomic=%d "
                "paragraphs=%d characters=%d",
                len(self._suggestions),
                len(atomic),
                len([part for part in text.split("\n\n") if part.strip()]),
                len(text),
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            # Say so in the log, not only on screen. This path emptied a
            # 25-section review of a 9630 character document on 2026-08-15 and
            # wrote nothing at all, so the app's own account of that essay was
            # a capture, a badge, and then 41 minutes of silence. A status
            # message the reader may not be looking at is not a diagnosis.
            logger.warning(
                "Writing HUD review preparation failed characters=%d: %s: %s",
                len(text),
                type(exc).__name__,
                exc,
            )
            self._suggestions = ()
            self._review_progress_total = 0
            self._render_suggestions()
            self._set_status(f"Local suggestions unavailable: {exc}", assertive=True)
            return
        self._render_suggestions()

    def _with_dictionary_spelling(
        self,
        text: str,
        atomic: tuple[object, ...],
    ) -> tuple[object, ...]:
        """Add the bundled dictionary's spelling repairs to the rule findings.

        A rule finding always wins where the two overlap: the rules understand
        context the speller does not, and a word already being repaired must
        not be repaired twice.
        """
        if self._spelling_engine is None:
            return atomic
        try:
            spelling = tuple(self._spelling_engine.analyze_spelling(text))
        except Exception:  # noqa: BLE001 - a missing dictionary must not
            # take the review card down; the rules still apply.
            return atomic
        if not spelling:
            return atomic
        claimed = [
            (getattr(value, "start", -1), getattr(value, "end", -1))
            for value in atomic
        ]
        extra = [
            value
            for value in spelling
            if not any(
                getattr(value, "start", -1) < end
                and start < getattr(value, "end", -1)
                for start, end in claimed
            )
        ]
        if not extra:
            return atomic
        return tuple(
            sorted(
                (*atomic, *extra),
                key=lambda value: getattr(value, "start", 0),
            )
        )

    def _review_readiness_label(self) -> str:
        """Whether every section in this review has a correction yet.

        Sections are prepared in the background, so a review can be open with
        most of it still being worked on. Anything waiting on the review -
        a reader, a screen reader, or automation - previously had to infer that
        from which buttons happened to be enabled.
        """
        total = len(self._suggestions)
        if not total:
            return "no sections"
        ready = 0
        refused = 0
        for suggestion in self._suggestions:
            identifier = str(getattr(suggestion, "suggestion_id", ""))
            if identifier in self._ai_completed_ids:
                ready += 1
            elif identifier in self._ai_failed_ids:
                refused += 1
        if ready + refused < total:
            return f"preparing, {ready} of {total} sections ready"
        if refused:
            # Finished is not the same as ready. Counting a section the AI
            # could not correct as "ready" told a reader every section had a
            # correction while the card in front of them said "AI correction
            # not ready" and Replace was disabled - a flat contradiction.
            return (
                f"all sections finished, {refused} of {total} "
                "could not be corrected"
            )
        return "all sections ready"

    def _available_suggestions(self) -> tuple[object, ...]:
        return tuple(
            suggestion
            for suggestion in self._suggestions
            if str(getattr(suggestion, "suggestion_id", "")) not in self._dismissed_ids
        )

    def _set_filter(self, category: str) -> None:
        button = self.filter_buttons.get(category)
        if button is not None and not button.isChecked():
            button.setChecked(True)
        self._render_suggestions()

    def _active_filter(self) -> str:
        for name, button in self.filter_buttons.items():
            if button.isChecked():
                return name
        return "All"

    def _render_suggestions(self) -> None:
        if not hasattr(self, "suggestion_list"):
            return
        available = self._available_suggestions()
        self._review_suggestions = available
        if self._review_index >= len(available):
            self._review_index = max(0, len(available) - 1)
        category = self._active_filter()
        visible = tuple(
            suggestion
            for suggestion in available
            if category == "All" or category in _category_names(suggestion)
        )
        self._visible_suggestions = visible
        self.suggestion_list.blockSignals(True)
        self.suggestion_list.clear()
        for suggestion in visible:
            item = QListWidgetItem(
                f"{' + '.join(_category_names(suggestion))} | "
                f"{str(suggestion.title).strip()}"
            )
            item.setData(Qt.ItemDataRole.UserRole, str(suggestion.suggestion_id))
            item.setToolTip(str(suggestion.explanation).strip())
            self.suggestion_list.addItem(item)
        if visible:
            current_id = str(
                getattr(self._current_review_suggestion(), "suggestion_id", "")
            )
            selected_row = next(
                (
                    index
                    for index, item in enumerate(visible)
                    if str(getattr(item, "suggestion_id", "")) == current_id
                ),
                0,
            )
            self.suggestion_list.setCurrentRow(selected_row)
        self.suggestion_list.blockSignals(False)
        total = len(available)
        filtered = len(visible)
        label = f"{total} review" + ("" if total == 1 else "s")
        if category != "All":
            label += f" | {filtered} {category.lower()}"
        self.suggestion_count.setText(
            f"{total} review" + ("" if total == 1 else "s")
        )
        self.suggestion_count.setAccessibleDescription(self.suggestion_count.text())
        self.assistant_suggestion_count.setText(label)
        self.assistant_suggestion_count.setAccessibleDescription(label)
        self._show_review_suggestion()
        self.assistant_apply_button.setEnabled(not self._busy and bool(visible))
        self.apply_all_button.setEnabled(not self._busy and bool(available))
        self._update_badge()

    def _show_review_suggestion(self) -> None:
        # A section back under review is no longer resolved. Accepting one
        # removes it and returns the reader to the section they had stepped
        # past, and that section is in play again - counting it as done would
        # overstate progress by exactly one for the rest of the review.
        current = self._current_review_suggestion()
        current_id = str(getattr(current, "suggestion_id", ""))
        if current_id:
            self._review_resolved.discard(current_id)
        self.after_heading.setText("Suggested text")
        self.rewrite_sentence_button.setText("Regenerate")
        total = len(self._review_suggestions)
        if not total:
            self.target_changed.emit(-1, -1)
            source = self._preview_base or self.source_edit.toPlainText()
            # Two different things end up here and they need different words.
            # Saying "No sentence available - capture editable text again" over
            # two blank panes and "0 of 0" told a writer nothing: they could not
            # tell a failed capture from a document with nothing to fix, and the
            # empty Original pane read as the app having lost their text.
            captured = bool(source.strip())
            if captured:
                self.suggestion_title.setText("Nothing to correct here")
                self.suggestion_explanation.setText(
                    "Manuscript found no spelling, grammar or punctuation "
                    "problem in this text."
                )
                self.before_text.setPlainText(source)
                self._set_suggested_text(source, source)
                self.card_position.setText("0 of 0")
            else:
                self.suggestion_title.setText("No text was captured")
                self.suggestion_explanation.setText(
                    "Manuscript could not read any text to review. Click in "
                    "your document, or select the part you want checked, and "
                    "press the Writing HUD shortcut again. A document opened "
                    "from email or a download may be read-only until you "
                    "choose Enable Editing."
                )
                # Blank panes with headings look like lost text. Hide them and
                # let the message carry the card.
                self.before_text.setPlainText("")
                self._set_suggested_text("", "")
                self.card_position.setText("")
            self.before_heading.setVisible(captured)
            self.before_text.setVisible(captured)
            self.after_heading.setVisible(captured)
            self.queue_heading.hide()
            self.queue_preview.hide()
            self.previous_button.setEnabled(False)
            self.next_button.setEnabled(False)
            self.apply_suggestion_button.setEnabled(False)
            self.rewrite_sentence_button.setEnabled(False)
            self.dismiss_suggestion_button.setEnabled(False)
            self.apply_changes_button.setEnabled(bool(self.result_text) and not self._busy)
            # A clean document, and a document whose last section has just been
            # accepted, both land here. Both should say so and then get out of
            # the way. A non-empty result_text does not hold the collapse back:
            # accepting a section already emitted the replacement, and
            # `_replace` only re-sends the same text.
            if captured and not self._busy:
                self._no_issues_timer.start()
            else:
                self._no_issues_timer.stop()
            return
        self._no_issues_timer.stop()
        # The empty-card branch above hides these, so restore them here rather
        # than leaving a real suggestion with no Original pane.
        self.before_heading.setVisible(True)
        self.before_text.setVisible(True)
        self.after_heading.setVisible(True)
        suggestion = self._review_suggestions[self._review_index]
        self._publish_current_target()
        sentence_count = int(getattr(suggestion, "sentence_count", 1))
        scope = f"{sentence_count} sentence" + ("" if sentence_count == 1 else "s")
        categories = _category_names(suggestion)
        category = categories[0] if categories else "Rewrite"
        heading = {
            "Spelling": "Correctness suggestion",
            "Grammar": "Correctness suggestion",
            "Formatting": "Formatting suggestion",
            "Clarity": "Clarity suggestion",
            "Rewrite": "Clarity suggestion",
        }.get(category, "Writing suggestion")
        self.suggestion_title.setText(heading)
        self.before_text.setPlainText(str(suggestion.original))
        replacement = str(suggestion.replacement)
        self._set_suggested_text(str(suggestion.original), replacement)
        explanation = str(suggestion.explanation).strip()
        explanation = f"Affects {scope}. {explanation}" if explanation else f"Affects {scope}."
        self.suggestion_explanation.setText(explanation)
        self.suggestion_explanation.setToolTip(explanation)
        self._render_queue_preview()
        overall_total = max(
            self._review_progress_total,
            self._review_progress_completed + total,
        )
        overall_position = min(
            overall_total,
            self._review_progress_completed
            + len(self._review_resolved)
            + 1,
        )
        progress = f"{overall_position} of {overall_total}"
        if progress != self._logged_progress:
            # The counter is the only thing that shows a section was skipped.
            # A user saw it go from 1 straight to 4 and the log said nothing,
            # so record every change with the numbers it was built from.
            previous = self._logged_progress or "start"
            self._logged_progress = progress
            logger.info(
                "Writing HUD section counter %s -> %s "
                "(completed=%d index=%d available=%d visible=%d total=%d)",
                previous,
                progress,
                self._review_progress_completed,
                self._review_index,
                len(self._review_suggestions),
                len(self._visible_suggestions),
                self._review_progress_total,
            )
        self.card_position.setText(progress)
        # Say whether the review is still loading. A reader using a screen
        # reader could not otherwise tell a section that has no correction yet
        # from one that has finished, and neither could anything driving the
        # HUD: automation had to infer readiness from button states and counter
        # text, sampling an asynchronous pipeline at fixed moments. Four
        # separate timing patches later, a different three or four documents
        # failed every run. This states the fact instead of leaving it guessed.
        readiness = self._review_readiness_label()
        self.card_position.setAccessibleName(
            f"Current writing suggestion position, {progress}, {readiness}"
        )
        self.card_position.setAccessibleDescription(f"{progress}, {readiness}")
        self.card_position.setVisible(overall_total > 1)
        self.card_position.adjustSize()
        self.card_position.updateGeometry()
        self.card_position.repaint()
        self.card_page.update()
        QTimer.singleShot(0, self._repaint_card_progress)
        QTimer.singleShot(50, self._repaint_card_progress)
        self.previous_button.setEnabled(not self._busy and self._review_index > 0)
        self.next_button.setEnabled(not self._busy and self._review_index + 1 < total)
        self.apply_suggestion_button.setEnabled(not self._busy)
        self.apply_suggestion_button.setText("Replace Text")
        self.apply_suggestion_button.setAccessibleName("Replace text in source box")
        self.apply_suggestion_button.setAccessibleDescription(
            "Replace the captured source text with the current correction."
        )
        suggestion_id = str(getattr(suggestion, "suggestion_id", ""))
        failure_message = self._ai_failure_messages.get(suggestion_id, "")
        if suggestion_id in self._ai_failed_ids:
            # Never label a deterministic placeholder as an AI correction.
            # The user can retry or dismiss while later sections keep working.
            self.suggestion_title.setText("AI correction not ready")
            self.after_heading.setText("AI correction not ready")
            self.after_text.clear()
            self.after_text.setPlaceholderText("")
            self.after_text.setToolTip("")
            explanation = (
                "This section does not have a finished AI correction yet. "
                "Retry the AI or dismiss this section. Later sections keep "
                "processing."
            )
            self.suggestion_explanation.setText(explanation)
            self.suggestion_explanation.setToolTip(explanation)
            self.apply_suggestion_button.setEnabled(False)
            self.apply_suggestion_button.setToolTip(
                failure_message or explanation
            )
            self.rewrite_sentence_button.setText("Retry")
            self.rewrite_sentence_button.setEnabled(True)
            self.tone_combo.setEnabled(True)
            self.dismiss_suggestion_button.setText("Dismiss")
            self.dismiss_suggestion_button.setEnabled(True)
            self._set_status(failure_message or explanation, assertive=True)
            if self._view == "loading":
                self._set_view("card", activate=True, reposition=False)
            if self._view == "card" and self.isVisible():
                self._apply_view_size()
                QTimer.singleShot(50, self._reassert_current_view_size)
            return
        self.dismiss_suggestion_button.setText("Dismiss")
        ready_message = self._ai_ready_messages.get(suggestion_id, "")
        if ready_message:
            self.apply_suggestion_button.setToolTip(ready_message)
            self.apply_suggestion_button.setAccessibleName(
                f"Replace text in source box. {ready_message}"
            )
            self.apply_suggestion_button.setAccessibleDescription(ready_message)
        self.rewrite_sentence_button.setEnabled(not self._busy)
        self.tone_combo.setEnabled(not self._busy)
        self.dismiss_suggestion_button.setEnabled(not self._busy)
        if (
            self._ai_proofread
            and suggestion_id
            # Only lock while the user is actually inside the review flow. A
            # badge or assistant render must not start AI work or lock
            # buttons - doing so fired a generation on every focus poll.
            and self._view in {"card", "loading"}
            and suggestion_id not in self._ai_completed_ids
            and suggestion_id not in self._ai_failed_ids
            and (
                # Ordered document review (Word / automatic HUD): never expose
                # a section until its AI result exists. Manual palette flows
                # keep rules-only Replace available and lock only while a
                # request for this exact suggestion is in flight.
                self._step_replacer is not None
                or suggestion_id == self._active_ai_suggestion_id
            )
        ):
            # The current section has no finished AI result yet - whether its
            # request is active, queued behind a prefetch, or not yet issued.
            # Never expose or enable the deterministic placeholder as an AI
            # result: a fast Accept here silently applied rules-only text.
            # Stay on the big card; only the controls lock while it finishes.
            self.after_text.clear()
            self.after_text.setPlaceholderText("AI correction is being prepared...")
            self.after_text.setToolTip("AI correction is being prepared.")
            if suggestion_id == self._active_ai_suggestion_id:
                self._active_ai_background = False
            elif (
                not self._active_ai_attempt_id
                and suggestion_id not in self._ai_requested_ids
            ):
                # Nothing is generating and this section was never requested;
                # issue its request now or the review would wait forever.
                source = str(getattr(suggestion, "replacement", "")).strip()
                if source:
                    self._ai_requested_ids.add(suggestion_id)
                    self._start_sentence_ai_request(
                        suggestion_id,
                        source,
                        self._selected_tone_command(automatic=True),
                        "AI proofread",
                    )
                    return
            self.set_busy(True, "AI proofread")
            if self._view != "card":
                self._set_view("loading", activate=True, reposition=False)
            return
        if self._view == "card" and self.isVisible():
            self._apply_view_size()
            QTimer.singleShot(50, self._reassert_current_view_size)

    def _set_current_review_status(self, fallback: str) -> None:
        suggestion = self._current_review_suggestion()
        suggestion_id = str(getattr(suggestion, "suggestion_id", ""))
        failure_message = self._ai_failure_messages.get(suggestion_id, "")
        self._set_status(
            failure_message or fallback,
            assertive=bool(failure_message),
        )

    def _render_queue_preview(self) -> None:
        upcoming = self._review_suggestions[self._review_index + 1 :]
        total = max(
            self._review_progress_total,
            self._review_progress_completed + len(self._review_suggestions),
        )
        if not upcoming:
            self.queue_heading.hide()
            self.queue_preview.hide()
            return
        rows = []
        for offset, suggestion in enumerate(upcoming, start=1):
            position = self._review_progress_completed + self._review_index + offset + 1
            original = " ".join(str(getattr(suggestion, "original", "")).split())
            suggestion_id = str(getattr(suggestion, "suggestion_id", ""))
            if suggestion_id in self._ai_completed_ids:
                replacement = " ".join(
                    str(getattr(suggestion, "replacement", "")).split()
                )
            elif suggestion_id in self._ai_failed_ids:
                replacement = "Correction failed quality checks. Retry when reached."
            else:
                replacement = "Processing with AI..."
            rows.append(
                f"Next: {position} of {total}\n"
                f"Original: {original}\n"
                f"Suggested: {replacement}"
            )
        self.queue_preview.setPlainText("\n\n".join(rows))
        self.queue_heading.show()
        self.queue_preview.show()

    def _repaint_card_progress(self) -> None:
        if self._view != "card" or not self.card_position.isVisible():
            return
        self.card_position.updateGeometry()
        self.card_position.repaint()
        self.card_page.update()

    def _set_suggested_text(self, original: str, replacement: str) -> None:
        visible = replacement if replacement.strip() else original
        if not visible.strip():
            visible = "No corrected text was returned. Keep the original text."
        self.after_text.setPlaceholderText("")
        self.after_text.setPlainText(visible)
        self.after_text.setToolTip(visible)
        visible_format = QTextCharFormat()
        visible_format.setForeground(QColor(COLORS["text"]))
        document_cursor = QTextCursor(self.after_text.document())
        document_cursor.select(QTextCursor.SelectionType.Document)
        document_cursor.mergeCharFormat(visible_format)
        document_cursor.clearSelection()
        document_cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.after_text.setTextCursor(document_cursor)
        changed = QTextCharFormat()
        changed.setBackground(QColor(COLORS["diff_insert_background"]))
        changed.setForeground(QColor(COLORS["diff_insert_text"]))
        changed.setFontWeight(QFont.Weight.Bold)
        added_selections: list[QTextEdit.ExtraSelection] = []
        removed = QTextCharFormat()
        removed.setBackground(QColor(COLORS["diff_remove_background"]))
        removed.setForeground(QColor(COLORS["diff_remove_text"]))
        removed.setFontStrikeOut(True)
        removed.setFontWeight(QFont.Weight.Bold)
        removed_selections: list[QTextEdit.ExtraSelection] = []
        for operation, i1, i2, j1, j2 in _token_diff_opcodes(original, visible):
            if operation == "equal":
                continue
            if operation in {"delete", "replace"} and i2 > i1:
                cursor = QTextCursor(self.before_text.document())
                cursor.setPosition(i1)
                cursor.setPosition(i2, QTextCursor.MoveMode.KeepAnchor)
                selection = QTextEdit.ExtraSelection()
                selection.cursor = cursor
                selection.format = removed
                removed_selections.append(selection)
            start, end = j1, j2
            if start == end and visible:
                start = max(0, min(len(visible) - 1, start - 1))
                end = start + 1
            if end <= start:
                continue
            cursor = QTextCursor(self.after_text.document())
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format = changed
            added_selections.append(selection)
        self.before_text.setExtraSelections(removed_selections)
        self.after_text.setExtraSelections(added_selections)

    def _restore_visible_suggestion(self, suggestion_id: str = "") -> None:
        suggestion = next(
            (
                item
                for item in self._review_suggestions
                if not suggestion_id
                or str(getattr(item, "suggestion_id", "")) == suggestion_id
            ),
            self._current_review_suggestion(),
        )
        if suggestion is None:
            source = self.source_edit.toPlainText()
            self._set_suggested_text(source, source)
            return
        self._set_suggested_text(
            str(getattr(suggestion, "original", "")),
            str(getattr(suggestion, "replacement", "")),
        )

    def _step_suggestion(self, offset: int) -> None:
        if self._busy or not self._review_suggestions:
            return
        if int(offset) > 0:
            # Moving on by choice counts; the review doing it for you does not.
            current = self._current_review_suggestion()
            current_id = str(getattr(current, "suggestion_id", ""))
            if current_id:
                self._review_resolved.add(current_id)
        self._review_index = min(
            max(0, self._review_index + int(offset)),
            len(self._review_suggestions) - 1,
        )
        self._show_review_suggestion()

    def _suggestion_selected(self, row: int) -> None:
        suggestion = (
            self._visible_suggestions[row]
            if 0 <= row < len(self._visible_suggestions)
            else None
        )
        self.assistant_apply_button.setEnabled(not self._busy and suggestion is not None)
        if suggestion is None:
            return
        suggestion_id = str(getattr(suggestion, "suggestion_id", ""))
        for index, item in enumerate(self._review_suggestions):
            if str(getattr(item, "suggestion_id", "")) == suggestion_id:
                self._review_index = index
                self._show_review_suggestion()
                break

    def _current_review_suggestion(self) -> object | None:
        if 0 <= self._review_index < len(self._review_suggestions):
            return self._review_suggestions[self._review_index]
        return None

    def _apply_current_suggestion(self) -> None:
        self._apply_suggestion_object(self._current_review_suggestion())

    def _apply_selected_suggestion(self) -> None:
        """Compatibility entry point used by older callers and tests."""
        self._apply_current_suggestion()

    def _apply_assistant_suggestion(self) -> None:
        row = self.suggestion_list.currentRow()
        suggestion = (
            self._visible_suggestions[row]
            if 0 <= row < len(self._visible_suggestions)
            else None
        )
        self._apply_suggestion_object(suggestion)

    def _apply_suggestion_object(self, suggestion: object | None) -> None:
        accepted_id = str(getattr(suggestion, "suggestion_id", ""))
        logger.info(
            "Writing HUD Replace Text invoked busy=%s index=%d reviews=%d "
            "suggestion=%s ai_completed=%s ai_failed=%s original_chars=%d "
            "replacement_chars=%d",
            self._busy,
            self._review_index,
            len(self._review_suggestions),
            suggestion is not None,
            accepted_id in self._ai_completed_ids,
            accepted_id in self._ai_failed_ids,
            len(str(getattr(suggestion, "original", ""))),
            len(str(getattr(suggestion, "replacement", ""))),
        )
        if self._busy or suggestion is None:
            return
        current_id = str(getattr(suggestion, "suggestion_id", ""))
        if self._active_ai_suggestion_id in {"", current_id}:
            self._cancel_active_ai_request()
        had_changes = bool(getattr(suggestion, "has_changes", True))
        try:
            updated = _apply_sentence_suggestion(self._preview_base, suggestion)
            remaining = (
                self._review_suggestions[: self._review_index]
                + self._review_suggestions[self._review_index + 1 :]
            )
            pending = _shift_remaining_sentence_suggestions(
                updated,
                suggestion,
                remaining,
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            logger.warning("Writing HUD Accept failed: %s", exc)
            self._set_status(str(exc), assertive=True)
            return
        changed_now = updated != self._preview_base
        if changed_now and self._step_replacer is not None:
            try:
                applied = bool(self._step_replacer(updated, suggestion))
            except (RuntimeError, TypeError, ValueError) as exc:
                logger.warning("Writing HUD immediate Accept failed: %s", exc)
                self._set_status(str(exc), assertive=True)
                return
            if not applied:
                logger.warning("Writing HUD immediate Accept was not applied")
                return
            self._accepted_external_changes = True
        self._preview_base = updated
        self._set_result_text(updated)
        self._dismissed_ids.clear()
        self._suggestions = pending
        previously_pending = len(self._review_suggestions)
        self._review_progress_completed = min(
            self._review_progress_total,
            self._review_progress_completed + 1,
        )
        self._review_index = 0
        # If accepting one section removes more than one pending section, the
        # counter jumps and the extra sections are never reviewed. That is
        # invisible without this line.
        dropped = previously_pending - 1 - len(pending)
        logger.info(
            "Writing HUD section accepted id=%s changed=%s pending %d -> %d "
            "dropped=%d completed=%d of %d",
            accepted_id or "none",
            changed_now,
            previously_pending,
            len(pending),
            dropped,
            self._review_progress_completed,
            self._review_progress_total,
        )
        if dropped:
            logger.warning(
                "Writing HUD dropped %d section(s) while accepting one; the "
                "counter will skip ahead",
                dropped,
            )
        self._render_suggestions()
        self._update_series_progress()
        if pending:
            message = (
                "Accepted in the document. Next text ready."
                if changed_now and self._step_replacer is not None
                else "Accepted. Next text ready."
                if had_changes
                else "Next text ready."
            )
            self._set_status(message)
            logger.info(
                "Writing HUD advanced after Accept pending=%d",
                len(pending),
            )
            self._set_current_review_status(message)
            self._request_ai_proofread_current()
            return
        if self._step_replacer is not None:
            self.finish_choice()
            if self._accepted_external_changes:
                self.applied.emit()
            else:
                self.unchanged.emit()
            return
        if updated != self.source_edit.toPlainText():
            self._set_status("Applying accepted sentences...")
            self.replace_requested.emit(updated)
        else:
            self.finish_choice()
            self.unchanged.emit()

    def _dismiss_current_suggestion(self) -> None:
        if self._busy or not self._review_suggestions:
            return
        current = self._current_review_suggestion()
        current_id = str(getattr(current, "suggestion_id", ""))
        if self._active_ai_suggestion_id in {"", current_id}:
            self._cancel_active_ai_request()
        if current_id:
            self._review_resolved.add(current_id)
        if current_id:
            self._review_resolved.add(current_id)
        if self._review_index + 1 < len(self._review_suggestions):
            self._review_index += 1
            self._show_review_suggestion()
            self._set_current_review_status("Dismissed. Next text ready.")
            self._request_ai_proofread_current()
            return
        if self._step_replacer is not None:
            self.finish_choice()
            if self._accepted_external_changes:
                self.applied.emit()
            else:
                self.unchanged.emit()
            return
        if self._preview_base != self.source_edit.toPlainText():
            self._set_status("Applying accepted sentences...")
            self.replace_requested.emit(self._preview_base)
        else:
            self.reject()

    def _apply_all(self) -> None:
        available = self._available_suggestions()
        if self._busy or not available:
            return
        self._cancel_active_ai_request()
        try:
            updated = _apply_all_sentence_suggestions(self._preview_base, available)
        except (RuntimeError, TypeError, ValueError) as exc:
            self._set_status(str(exc), assertive=True)
            return
        self._dismissed_ids.clear()
        self._accept_suggestion_preview(
            updated,
            "All sentence changes accepted into the draft. Apply changes when ready.",
        )

    def _accept_suggestion_preview(self, text: str, message: str) -> None:
        self._preview_base = text
        self._set_result_text(text)
        self._analyze_suggestions(text)
        self._set_status(message)

    def _set_result_text(self, text: str) -> None:
        self._updating_result = True
        try:
            self.result_edit.setPlainText(text)
        finally:
            self._updating_result = False
        self._refresh_decision_buttons()

    def _result_edited(self) -> None:
        self._refresh_decision_buttons()
        if self._updating_result:
            return
        self._preview_base = self.result_edit.toPlainText()
        self._dismissed_ids.clear()
        self._analyze_suggestions(self._preview_base)

    def _refresh_decision_buttons(self) -> None:
        enabled = bool(self.result_text.strip()) and not self._busy
        self.replace_button.setEnabled(enabled)
        self.apply_changes_button.setEnabled(enabled)
        self.stage_button.setEnabled(enabled)

    def _update_scope_controls(self) -> None:
        words = len(self.source_edit.toPlainText().split())
        scope = "Entire document" if self._document_scope else "Current selection"
        detail = f"{scope} | {words} words"
        self.scope_label.setText(detail)
        self.assistant_scope_label.setText(detail)
        can_expand = not self._document_scope
        self.document_button.setEnabled(can_expand)
        self.assistant_document_button.setEnabled(can_expand)
        self.document_button.setText(
            "Loaded" if self._document_scope else "Document"
        )
        self.assistant_document_button.setText(
            "Entire document loaded" if self._document_scope else "Check entire document"
        )
        self.replace_button.setText(
            "Apply changes to document" if self._document_scope else "Apply changes to selection"
        )
        self.apply_changes_button.setText(
            "Apply to document" if self._document_scope else "Apply changes"
        )

    def _replace(self) -> None:
        if self.result_text.strip() and not self._busy:
            self.replace_requested.emit(self.result_text)

    def _stage(self) -> None:
        if self.result_text.strip() and not self._busy:
            self.stage_requested.emit(self.result_text)

    def reject(self) -> None:
        had_content = bool(self.source_edit.toPlainText() or self.result_edit.toPlainText())
        self.finish_choice()
        if had_content:
            self.rejected.emit()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._target_marking_enabled = False
        self._user_opened = False
        self.target_changed.emit(-1, -1)
        self._analysis_timer.stop()
        self._pulse_timer.stop()
        self._ai_timeout_timer.stop()
        self._ai_progress_timer.stop()
        self._active_ai_attempt_id = 0
        self._active_ai_suggestion_id = ""
        self._analysis_pending = False
        if not self._closing_after_choice:
            had_content = bool(self.source_edit.toPlainText() or self.result_edit.toPlainText())
            self.source_edit.clear()
            self._set_result_text("")
            self._preview_base = ""
            self._suggestions = ()
            self._dismissed_ids.clear()
            self._ai_completed_ids.clear()
            self._ai_request_started.clear()
            self._ai_duration_samples.clear()
            self._render_suggestions()
            if had_content:
                self.rejected.emit()
        event.accept()

    def _publish_current_target(self) -> None:
        if not self._target_marking_enabled or self._view == "badge":
            self.target_changed.emit(-1, -1)
            return
        suggestion = self._current_review_suggestion()
        if suggestion is None:
            self.target_changed.emit(-1, -1)
            return
        self.target_changed.emit(
            int(getattr(suggestion, "start", -1)),
            int(getattr(suggestion, "end", -1)),
        )

    def _set_status(self, text: str, *, assertive: bool = False) -> None:
        update_status_label(self.status, text, assertive=assertive)
        update_status_label(self.assistant_status, text, assertive=assertive)
        update_status_label(self.loading_status, text, assertive=assertive)
        self.status.show()
        if self._view == "card" and self.isVisible():
            QTimer.singleShot(0, self._reassert_current_view_size)

    def _suggestion_status(self, prefix: str) -> str:
        total = len(self._available_suggestions())
        suffix = f"{total} review" + ("" if total == 1 else "s") + " ready"
        return f"{prefix} {suffix}."

    def _show_near_pointer(
        self,
        *,
        activate: bool = True,
        anchor: tuple[int, int, int, int] | None = None,
    ) -> None:
        self._anchor = anchor
        self._apply_view_size()
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, not activate)
        self.show()
        self._apply_view_size()
        self._position_on_screen(anchor=anchor)
        if activate:
            self.raise_()
            self.activateWindow()
        else:
            self._keep_target_active()
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        QTimer.singleShot(50, self._reassert_current_view_size)

    def _reassert_current_view_size(self) -> None:
        if not self.isVisible():
            return
        self._apply_view_size()
        if self._user_positioned:
            self._clamp_current_position()
        else:
            self._position_on_screen(anchor=self._anchor)

    def _position_on_screen(
        self,
        *,
        anchor: tuple[int, int, int, int] | None,
    ) -> None:
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        width = self.width()
        height = self.height()
        if anchor is None:
            target_x = QCursor.pos().x() - width // 2
            target_y = QCursor.pos().y() + 14
        else:
            left, top, right, bottom = anchor
            if sys.platform == "win32":
                scale = max(1.0, float(screen.devicePixelRatio()))
                left, top, right, bottom = (
                    round(left / scale),
                    round(top / scale),
                    round(right / scale),
                    round(bottom / scale),
                )
            # The badge sits to the right of the text it was captured from.
            #
            # Two earlier attempts got this wrong in ways worth recording.
            # Anchoring outside the right edge works for a selection
            # rectangle, but the anchor is usually a whole text box whose
            # right edge is already at the window edge, so there is no room:
            # the first version then jumped to the left of the box, and the
            # second parked the badge at the far right of the screen, which is
            # on the correct side but nowhere near the writing.
            #
            # When it will not fit outside, it goes just inside the anchor's
            # right edge instead, which is beside the text in the reader's
            # sense of the word.
            target_x = right + 8
            if target_x + width - 1 > area.right():
                target_x = right - width - 8
            target_y = top + ((bottom - top) - height) // 2
        x = min(max(target_x, area.left()), area.right() - width + 1)
        y = min(max(target_y, area.top()), area.bottom() - height + 1)
        self.move(x, y)

    def _clamp_current_position(self) -> None:
        screen = QApplication.screenAt(self.pos()) or QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        x = min(max(self.x(), area.left()), area.right() - self.width() + 1)
        y = min(max(self.y(), area.top()), area.bottom() - self.height() + 1)
        self.move(x, y)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.position().toPoint())
            visible_header = (
                self._view != "card"
                or event.position().y() <= self.close_button.geometry().bottom() + 2
            )
            if visible_header and not isinstance(
                child,
                (QPushButton, QLineEdit, QPlainTextEdit, QTextEdit, QListWidget),
            ):
                self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            self._user_positioned = True
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            self._user_positioned = True
            self._clamp_current_position()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _keep_target_active(self) -> None:
        """Keep an automatically shown HUD topmost without taking keyboard focus."""
        if sys.platform != "win32":
            return
        application = QApplication.instance()
        if application is not None and application.platformName().casefold() == "offscreen":
            return
        hwnd = int(self.winId())
        if not hwnd:
            return
        swp_no_size = 0x0001
        swp_no_move = 0x0002
        swp_no_activate = 0x0010
        swp_show_window = 0x0040
        ctypes.windll.user32.SetWindowPos(
            hwnd,
            -1,
            0,
            0,
            0,
            0,
            swp_no_size | swp_no_move | swp_no_activate | swp_show_window,
        )










# Whitespace runs, words (letters, digits and inner apostrophes), or a single
# punctuation mark. This is Android's InlineEditDiff tokenisation.
























def _category_names(suggestion: object) -> tuple[str, ...]:
    categories = getattr(suggestion, "categories", None)
    if categories:
        return tuple(str(item) for item in categories)
    return (_category_name(suggestion),)




_HUD_STYLESHEET = f"""
QWidget#hudBadgePage {{
    background: transparent;
}}
QPushButton#hudBadgeButton {{
    min-width: 32px;
    min-height: 32px;
    padding: 0;
    border: 2px solid {COLORS['accent']};
    border-radius: 16px;
    background: {COLORS['window']};
    color: {COLORS['text']};
    font-size: 12pt;
    font-weight: 750;
}}
QPushButton#hudBadgeButton:hover,
QPushButton#hudBadgeButton:focus {{
    border-color: {COLORS['accent_text']};
    background: {COLORS['accent_soft']};
}}
QFrame#hudLoading, QFrame#hudCard, QFrame#hudAssistant {{
    background: {COLORS['window']};
    border: 1px solid {COLORS['border']};
    border-radius: 12px;
}}
QFrame#hudLoading QPushButton,
QFrame#hudCard QPushButton {{
    min-height: 30px;
    max-height: 32px;
    padding: 0 8px;
}}
QFrame#hudCard QPushButton#primaryButton {{
    min-height: 32px;
    max-height: 32px;
    font-size: 10pt;
}}
QFrame#hudCard QComboBox {{
    min-height: 30px;
    max-height: 32px;
    padding: 0 7px;
}}
QLabel#hudTitle {{
    color: {COLORS['text']};
    font-size: 13pt;
    font-weight: 750;
}}
QLabel#hudCount {{
    color: {COLORS['accent_text']};
    background: {COLORS['window']};
    padding: 1px 3px;
    border-radius: 4px;
    font-size: 9pt;
    font-weight: 700;
}}
QLabel#hudSuggestionTitle {{
    color: {COLORS['text']};
    font-size: 11pt;
    font-weight: 700;
}}
QLabel#hudEyebrow {{
    color: {COLORS['muted']};
    font-size: 8pt;
    font-weight: 750;
}}
QPlainTextEdit#hudTextBox, QTextEdit#hudTextBoxAccent {{
    min-height: 40px;
    padding: 4px 6px;
    border: 1px solid {COLORS['border']};
    border-radius: 7px;
    background: {COLORS['surface']};
    color: {COLORS['text']};
}}
QTextEdit#hudTextBoxAccent {{
    border-color: {COLORS['border']};
    background: {COLORS['surface']};
}}
QLabel#hudExplanation, QLabel#hudStatus {{
    color: {COLORS['muted']};
}}
QLabel#hudStatus {{
    min-height: 22px;
}}
QPushButton#hudQuietButton {{
    min-width: 66px;
    min-height: 32px;
    padding: 0 9px;
}}
QPushButton#hudTextButton {{
    color: {COLORS['muted']};
    background: transparent;
    border-color: transparent;
}}
QPushButton#hudTextButton:hover {{
    color: {COLORS['text']};
    background: {COLORS['hover']};
}}
QPushButton[hudFilter="true"] {{
    min-width: 0px;
    min-height: 32px;
    padding: 0 8px;
}}
QPushButton[hudFilter="true"]:checked {{
    background: {COLORS['accent_dark']};
    border-color: {COLORS['accent']};
    font-weight: 700;
}}
"""


__all__ = ["RewritePalette"]
