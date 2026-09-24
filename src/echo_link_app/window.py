from __future__ import annotations

from dataclasses import replace as dataclass_replace
import collections
import ctypes
import hashlib
import os
import sys
import threading
import logging
import math
import time
from contextlib import nullcontext
from datetime import datetime
from difflib import ndiff

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStackedWidget,
    QSystemTrayIcon,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from echo_link_app.settings_dialog import SettingsDialog
from echo_link_app.accessibility import set_accessible, update_status_label
from echo_link_app.ai_status import user_ai_status
from echo_link_app.branding import APP_NAME, APP_SHORT_NAME, APP_TAGLINE, ICON_ICO, ICON_PNG
from echo_link_app.documents_panel import DocumentEditorPanel
from echo_link_app.drafts_panel import DraftsPanel
from echo_link_app.echo_bar import EchoBar
from echo_link_app.learning_dialog import LearningProposalDialog
from echo_link_app.rewrite_palette import RewritePalette
from echo_link_app.sound_cues import play_start, play_stop
from echo_link_app.startup import configure_startup
from echo_link_app.target_underline import TargetUnderline
from echo_link_app.theme import COLORS
from echo_link_core.audio import AudioCaptureError, play_pcm16, stop_audio_playback
from echo_link_core.accepted_inference import AcceptedInferenceCoordinator
from echo_link_core.engine import DictationEngine
from echo_link_core.documents import DocumentWorkspace
from echo_link_core.evaluation import export_evaluation_run, load_evaluation_cases, summarize_evaluation
from echo_link_core.hotkeys import GlobalPushToTalk
from echo_link_core.local_chat import ApprovedSourceChat, LocalChatResult
from echo_link_core.local_sources import LocalSourceIndex
from echo_link_core.media_control import MediaPauseController
from echo_link_core.insertion import (
    SelectionRecord,
    WindowsTextInserter,
    active_window,
    record_covers_entire_document,
    record_uses_word_document_adapter,
)
from echo_link_core.polish_readiness import (
    _polish_result_ready_for_review,
    _runtime_status_matches_plan,
)
from echo_link_core.models import (
    DictationMode,
    InsertionMode,
    SessionEvent,
    SessionState,
)
from echo_link_core.paths import resource_path
from echo_link_core.plagiarism import compare_local_sources
from echo_link_core.polish import (
    PolishWritingAssistantAdapter,
    create_default_polish_engine,
)
from echo_link_core.polish_types import (
    ClassifierDecision,
    ContextScope,
    GenerationRequest,
    PolishMode,
    PolishTask,
)
from echo_link_core.readability import analyze_readability
from echo_link_core.selected_text import (
    COMMANDS,
    PROFILES,
)
from echo_link_core.writing_models import LocalWritingModelManager
from echo_link_core.speech_logs import analyze_punctuation, format_punctuation_analysis
from echo_link_core.private_text_buffer import PrivateTextBuffer
from echo_link_core.version import build_commit
from echo_link_core.windows_selection import (
    DetectedSelection,
    WindowsSelectionMonitor,
    WindowsSelectionReader,
)


logger = logging.getLogger(__name__)

# The HUD reads up to 500,000 characters because it has to correct all of it.
# This read only has to recognise how a name is spelled, and it happens once
# per dictation, so it asks for far less. correction.py bounds what it actually
# scans again on its own side.
DOCUMENT_CONTEXT_CHARACTERS = 20_000





class _AcceptedPlanWritingAssistant:
    """Share each completed adaptive plan with every local writing consumer."""

    def __init__(self, delegate, coordinator, result_callback) -> None:
        self._delegate = delegate
        self._coordinator = coordinator
        self._result_callback = result_callback

    def __getattr__(self, name):
        return getattr(self._delegate, name)

    def run(self, *args, **kwargs):
        token = self._coordinator.begin_update()
        try:
            output = self._delegate.run(*args, **kwargs)
            result = getattr(self._delegate, "last_correction_result", None)
            published = self._coordinator.publish_correction_result(result, token)
            self._result_callback(
                result,
                published=published,
            )
            return output
        finally:
            self._coordinator.finish_update(token)


def _restore_native_window(handle: int) -> None:
    """Restore and foreground a tray-owned window on Windows."""
    if sys.platform != "win32" or int(handle) <= 0:
        return
    try:
        user32 = ctypes.windll.user32
        user32.ShowWindow(int(handle), 9)  # SW_RESTORE
        user32.BringWindowToTop(int(handle))
        user32.SetForegroundWindow(int(handle))
    except Exception:
        logger.exception("Could not restore Manuscript from the system tray")


# How long to keep trying to prewarm when the GPU has no room yet. A relaunch
# releases the previous copy's VRAM in seconds, so a few spaced retries cover
# it without keeping a thread alive on a machine that has no model to load.
_WARM_RETRY_ATTEMPTS = 4
_WARM_RETRY_DELAY_SECONDS = 20.0

# How many times Replace Text may fail on one correction before the HUD stops
# advising a retry and asks for a fresh capture. One failure is usually a
# repaint or focus race and retrying works; a second means the source moved.
_APPLY_RETRY_BEFORE_RECAPTURE = 2

# How long automatic capture stays quiet after asking for a fresh capture, so
# the selection that just failed is not immediately picked up again.
_RECAPTURE_SUPPRESS_SECONDS = 3.0




def _unique_document_text(document: str, expected: str) -> str | None:
    """Find one inserted selection across common Windows newline representations."""
    candidates = tuple(
        dict.fromkeys(
            (
                expected,
                expected.replace("\n", "\r"),
                expected.replace("\n", "\r\n"),
                expected.replace("\r\n", "\n"),
                expected.replace("\r", "\n"),
            )
        )
    )
    matches = [candidate for candidate in candidates if candidate and document.count(candidate) == 1]
    return matches[0] if len(matches) == 1 else None



def _is_the_same_capture(current, incoming) -> bool:
    """Is this focus signal about the same work as the review already in hand?

    THE SAME DOCUMENT WITH A MOVED CARET IS NOT A NEW CAPTURE. This used to be
    `current == record`, comparing whole SelectionRecords - which include the
    caret anchor. So an identical document reported again with the caret one
    character further along compared unequal, the guard did not fire, and the
    prepared review was thrown away and rebuilt from nothing.

    Measured across 98 automated runs of two documents on 2026-09-04 and 05.
    Twenty-two runs accepted NOTHING at all, and the separation is total:

        succeeded (76): the next focus signal arrived 22.6s to 57.5s later
        failed    (22): it arrived 3.3s to 4.7s later

    In all 22 failures the replacing capture was the same size as the one it
    replaced, and in all 22 the caret anchor had moved - x from 801 to 915 or
    993, y identical. Nothing had been typed. The review was discarded because
    the caret had shifted inside an unchanged document.

    That race is why the same corpus and the same product code scored 66.7 on
    one baseline run and 33.3 on the next, and why 36 experiments in a row
    discarded at 33.3 against a bar of 66.7 - the metric was measuring a coin
    flip, and every one of those discards was noise.

    Compared here: the window, the scope, the text, and the digest when both
    carry one. Not the anchor, and not the selection offsets, because those
    describe where the writer is rather than what there is to correct.
    """
    if current is None or incoming is None:
        return False
    if current.text != incoming.text or current.scope != incoming.scope:
        return False
    if (
        current.window.handle != incoming.window.handle
        or current.window.process_id != incoming.window.process_id
    ):
        return False
    if current.document_digest and incoming.document_digest:
        return current.document_digest == incoming.document_digest
    return True


def _normalized_document_text(value: str) -> str:
    """One spelling of a document's text, for comparing two captures of it.

    Word reports the same document with different line endings and trailing
    whitespace depending on how it was read, so a raw comparison of two
    captures of one unchanged document can differ.
    """
    return str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def _same_document_text(document: str, expected: str) -> bool:
    normalize = lambda value: value.replace("\r\n", "\n").replace("\r", "\n").strip()
    return normalize(document) == normalize(expected)




class WindowBridge(QObject):
    session_event = Signal(object)
    level_changed = Signal(float)
    hotkey_pressed = Signal(object)
    hotkey_released = Signal()
    hands_free_pressed = Signal()
    hands_free_cancel = Signal()
    writing_command_pressed = Signal()
    automatic_selection_detected = Signal(object)
    automatic_selection_cleared = Signal()
    private_paste_pressed = Signal()
    writing_result = Signal(object)
    palette_writing_result = Signal(object)
    palette_sentence_result = Signal(object)
    palette_sentence_progress = Signal(object)
    source_report = Signal(str)
    source_chat_result = Signal(object)
    hotkey_error = Signal(str)


class SectionStack(QStackedWidget):
    """Stacked pages with the small tab-label API used by window helpers and tests."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._section_names: list[str] = []

    def addTab(self, widget: QWidget, name: str) -> int:
        index = self.addWidget(widget)
        self._section_names.append(str(name))
        return index

    def tabText(self, index: int) -> str:
        if 0 <= index < len(self._section_names):
            return self._section_names[index]
        return ""


class EchoLinkWindow(QMainWindow):
    def __init__(
        self,
        store,
        *,
        enable_hotkey: bool = True,
        enable_tray: bool | None = None,
        internal_evaluation: bool = False,
    ):
        super().__init__()
        self.store = store
        self.bridge = WindowBridge(self)
        self.engine = DictationEngine(
            store,
            event_callback=self.bridge.session_event.emit,
            level_callback=self.bridge.level_changed.emit,
            document_context_provider=self._read_document_context,
        )
        self.hotkey: GlobalPushToTalk | None = None
        self.hands_free_hotkey: GlobalPushToTalk | None = None
        self.cancel_hotkey: GlobalPushToTalk | None = None
        self.command_hotkey: GlobalPushToTalk | None = None
        self.selection_monitor: WindowsSelectionMonitor | None = None
        self.private_paste_hotkey: GlobalPushToTalk | None = None
        self.private_text_buffer = PrivateTextBuffer()
        self.private_paste_inserter = WindowsTextInserter(InsertionMode.DIRECT)
        self.polish_engine = create_default_polish_engine(
            store=self.store,
            preferred_pack_provider=self._held_pack_id,
        )
        self.ai_coordinator = AcceptedInferenceCoordinator(self.polish_engine.runtime)
        self.polish_engine.runtime = self.ai_coordinator
        try:
            for pack in self.polish_engine.packs.catalog(refresh_installed=False):
                self.ai_coordinator.register_model_pack(pack.pack_id, pack.model)
        except (OSError, RuntimeError, TypeError, ValueError):
            logger.warning("Could not register signed local AI pack identities", exc_info=True)
        self.engine.set_ai_coordinator(self.ai_coordinator)
        self.engine.set_ai_plan_validator(self._dictation_ai_plan_is_current)
        self.polish_writing_adapter = PolishWritingAssistantAdapter(
            self.polish_engine,
            mode_provider=lambda: self.engine.settings.polish_mode,
            consent_provider=lambda: self.engine.settings.polish_download_consent,
        )
        # Keep the mature UI boundary stable while routing its work through PolishEngine.
        self.writing_assistant = _AcceptedPlanWritingAssistant(
            self.polish_writing_adapter,
            self.ai_coordinator,
            self._share_completed_polish_result,
        )
        self._installed_writing_models: set[str] = set()
        # Why the writing AI is not serving corrections, in the writer's own
        # words, or "" when it is. _show_ready_state reported the hotkeys and
        # the speech model and said nothing about this one, so a machine that
        # came up rules-only looked entirely healthy until a review card
        # rendered with no AI in it.
        self._writing_ai_reason: str = ""
        # Lets the prewarm retry give up at once when the window closes,
        # instead of sitting out its delay in a daemon thread.
        self._warm_retry_stop = threading.Event()
        # Consecutive Replace Text failures on the current review, so a source
        # that has genuinely moved stops being retried forever.
        self._failed_apply_streak = 0
        if os.environ.get("ECHOLINK_DISABLE_MODEL_SETUP") != "1":
            threading.Thread(
                target=self._refresh_installed_writing_models,
                daemon=True,
                name="echo-link-tone-model-discovery",
            ).start()
            threading.Thread(
                target=self._warm_adaptive_polish_until_ready,
                daemon=True,
                name="manuscript-adaptive-polish-setup",
            ).start()
        self.document_workspace = DocumentWorkspace(store)
        self.local_source_index = LocalSourceIndex()
        self.source_chat = ApprovedSourceChat(inference=self.ai_coordinator)
        self._source_chat_request_id = 0
        self._palette_request_id = 0
        self._palette_ai_lock = threading.Lock()
        self._palette_ai_queue: collections.deque = collections.deque()
        self._palette_ai_workers = 0
        self._palette_ai_shutdown = False
        sample = self.store.get_meta("writing_runtime_eta", {})
        self._palette_eta_sample = sample if isinstance(sample, dict) else {}
        self._automatic_hud_suppress_until = 0.0
        # Text that has already been through a complete review, so the badge
        # does not offer a finished document back to the writer. Keyed on the
        # text itself rather than on a timer: if the writer never edits, it
        # should never be re-offered, and the moment they do edit the text no
        # longer matches and the offer returns on its own.
        self._reviewed_texts: collections.deque = collections.deque(maxlen=8)
        self.selected_text_record = None
        if hasattr(self.engine.inserter, "set_selection_provider"):
            self.engine.inserter.set_selection_provider(self._read_current_writing_record)
        if hasattr(self.engine.inserter, "set_selection_restorer"):
            self.engine.inserter.set_selection_restorer(self._restore_writing_selection)
        self._badge_clear_timer: QTimer | None = None
        self._badge_clear_pending_record = None
        # A HUD left open after its source editor closes keeps global focus
        # forever, which silently breaks every later capture on the machine.
        self._hud_source_watchdog = QTimer(self)
        self._hud_source_watchdog.setInterval(2_000)
        self._hud_source_watchdog.timeout.connect(self._close_hud_if_source_gone)
        self._hud_source_watchdog.start()
        self.echo_bar = EchoBar(
            x=self.engine.settings.echo_bar_x,
            y=self.engine.settings.echo_bar_y,
            position_changed=self._save_echo_bar_position,
        )
        self.media_pause = MediaPauseController(
            enabled=os.environ.get("ECHOLINK_DISABLE_MEDIA_CONTROL") != "1"
        )
        self.tray_icon: QSystemTrayIcon | None = None
        self._tray_enabled = bool(enable_hotkey if enable_tray is None else enable_tray)
        self._tray_enabled = self._tray_enabled and os.environ.get("ECHOLINK_DISABLE_TRAY") != "1"
        self._quitting = False
        self._shutdown_complete = False
        self.internal_evaluation = bool(internal_evaluation)
        self._last_session_state = SessionState.READY
        self.enable_hotkey = enable_hotkey and os.environ.get("ECHOLINK_DISABLE_HOTKEY") != "1"
        self.latest_text = ""
        self.latest_raw_text = ""
        self.evaluation_cases = load_evaluation_cases()
        self.evaluation_run_id: int | None = None
        self.evaluation_trial_id: int | None = None
        self.evaluation_original_text = ""

        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(720, 520)
        self.resize(900, 720)
        icon_path = resource_path(*ICON_ICO)
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self._build_ui()
        self.target_underline = TargetUnderline()
        self.rewrite_palette = RewritePalette(ai_proofread=True)
        self.rewrite_palette.command_requested.connect(self._run_palette_command)
        self.rewrite_palette.sentence_rewrite_requested.connect(
            self._run_palette_sentence_rewrite
        )
        self.rewrite_palette.set_step_replacer(self._replace_palette_step)
        self.rewrite_palette.set_undo_replacer(self._undo_palette_step)
        self.rewrite_palette.set_ai_width_provider(self._palette_ai_width)
        self.rewrite_palette.replace_requested.connect(self._replace_palette_result)
        self.rewrite_palette.stage_requested.connect(self._stage_palette_result)
        self.rewrite_palette.document_requested.connect(self._capture_entire_document_for_palette)
        self.rewrite_palette.settings_requested.connect(self._open_settings)
        self.rewrite_palette.unchanged.connect(self._finish_palette_unchanged)
        self.rewrite_palette.applied.connect(self._finish_palette_applied)
        self.rewrite_palette.rejected.connect(self._reject_palette_result)
        self.rewrite_palette.target_changed.connect(self._show_palette_target)
        self.rewrite_palette.badge_offset_changed.connect(self._save_badge_offset)
        # Restore the remembered placement before the badge is ever shown.
        _offset_x = self.engine.settings.hud_badge_offset_x
        _offset_y = self.engine.settings.hud_badge_offset_y
        if _offset_x is not None and _offset_y is not None:
            self.rewrite_palette.set_badge_offset((int(_offset_x), int(_offset_y)))
        # Pause on mouse-down. Waiting for clicked (mouse-up) lets the source
        # editor report focus loss and close the badge before the click lands.
        self.rewrite_palette.badge_button.pressed.connect(self._pause_selection_monitor)
        if self.internal_evaluation:
            self.setWindowTitle(f"{APP_NAME} - Internal Evaluation")
            self._show_tab("Internal evaluation")
        self._configure_accessibility()
        self._build_tray()
        self._connect_bridge()
        self._set_mode(self.engine.settings.mode, save=False)
        self._refresh_vocabulary()
        self._refresh_snippets()
        self._refresh_evaluation()
        self._refresh_recovery()
        self._refresh_history(update_dictate=True)
        self.speech_log_purge_timer = QTimer(self)
        self.speech_log_purge_timer.setInterval(60 * 60 * 1000)
        self.speech_log_purge_timer.timeout.connect(self._purge_expired_speech_logs)
        self.speech_log_purge_timer.start()
        self.palette_focus_timer = QTimer(self)
        self.palette_focus_timer.setInterval(175)
        self.palette_focus_timer.timeout.connect(self._check_palette_focus)
        if self.enable_hotkey:
            self.palette_focus_timer.start()
        self._start_hotkey()
        self._start_selection_monitor()
        if self.enable_hotkey:
            self.engine.arm_microphone()
        self._show_ready_state()

    @property
    def tray_enabled(self) -> bool:
        return self.tray_icon is not None

    def _build_tray(self) -> None:
        if not self._tray_enabled:
            return
        app = QApplication.instance()
        if app is not None:
            app.setQuitOnLastWindowClosed(False)
        icon = QIcon(str(resource_path(*ICON_ICO)))
        tray = QSystemTrayIcon(icon, self)
        tray.setToolTip("Manuscript - Ready")
        menu = QMenu(self)
        self.tray_show_action = QAction("Show Manuscript", self)
        self.tray_dictate_action = QAction("Dictate in Manuscript", self)
        self.tray_hands_free_action = QAction("Start hands-free dictation", self)
        self.tray_command_action = QAction("Writing HUD for selected text", self)
        self.tray_settings_action = QAction("Settings", self)
        self.tray_quit_action = QAction("Quit Manuscript", self)
        self.tray_show_action.triggered.connect(self._show_from_tray)
        self.tray_dictate_action.triggered.connect(self._dictate_from_tray)
        self.tray_hands_free_action.triggered.connect(self._hands_free_from_tray)
        self.tray_command_action.triggered.connect(self._show_writing_tab)
        self.tray_settings_action.triggered.connect(self._open_settings_from_tray)
        self.tray_quit_action.triggered.connect(self._quit_from_tray)
        menu.addAction(self.tray_show_action)
        menu.addAction(self.tray_dictate_action)
        menu.addAction(self.tray_hands_free_action)
        menu.addAction(self.tray_command_action)
        menu.addSeparator()
        menu.addAction(self.tray_settings_action)
        menu.addSeparator()
        menu.addAction(self.tray_quit_action)
        tray.setContextMenu(menu)
        tray.activated.connect(self._tray_activated)
        tray.show()
        self.tray_icon = tray

    def _tray_activated(self, reason) -> None:
        if reason in {
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        }:
            self._show_from_tray()

    def _show_from_tray(self) -> None:
        state = self.windowState()
        if state & Qt.WindowState.WindowMinimized:
            self.setWindowState(state & ~Qt.WindowState.WindowMinimized)
        self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        _restore_native_window(int(self.winId()))

    def _dictate_from_tray(self) -> None:
        self._show_from_tray()
        self.engine.toggle_recording(insert_result=False)

    def _hands_free_from_tray(self) -> None:
        self._show_from_tray()
        if self.engine.hands_free_session:
            self.engine.stop_recording()
        elif self.engine.can_start_recording:
            self.engine.start_recording(insert_result=False, hands_free=True)

    def _open_settings_from_tray(self) -> None:
        self._show_from_tray()
        self._open_settings()

    def _show_writing_tab(self) -> None:
        self._show_from_tray()
        self.tabs.setCurrentIndex(1)
        update_status_label(
            self.writing_state,
            "Highlight text in another app, then press the Writing HUD hotkey.",
        )

    def _quit_from_tray(self) -> None:
        self._quitting = True
        self.close()
        if not self._shutdown_complete:
            self._quitting = False
            return
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        header = QFrame()
        header.setObjectName("headerBand")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 8, 10, 8)
        header_layout.setSpacing(10)
        icon = QLabel()
        icon.setFixedSize(72, 72)
        icon.setPixmap(QIcon(str(resource_path(*ICON_PNG))).pixmap(68, 68))
        icon.setAccessibleName("Manuscript logo")
        header_layout.addWidget(icon)
        heading = QVBoxLayout()
        heading.setSpacing(1)
        title = QLabel(APP_SHORT_NAME)
        title.setObjectName("appTitle")
        subtitle = QLabel(APP_TAGLINE)
        subtitle.setObjectName("appSubtitle")
        subtitle.setWordWrap(True)
        heading.addWidget(title)
        heading.addWidget(subtitle)
        header_layout.addLayout(heading, 1)
        local_badge = QLabel("LOCAL ONLY")
        local_badge.setObjectName("privacyBadge")
        local_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        local_badge.setAccessibleName("Local only privacy status")
        header_layout.addWidget(local_badge)
        self.settings_button = QPushButton("Settings")
        self.settings_button.setToolTip("Open Manuscript settings")
        self.settings_button.clicked.connect(self._open_settings)
        header_layout.addWidget(self.settings_button)
        root.addWidget(header)

        root.addWidget(self._build_global_status_band())

        workspace = QHBoxLayout()
        workspace.setSpacing(10)
        self.section_nav = QListWidget()
        self.section_nav.setObjectName("sectionNav")
        self.section_nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.section_nav.setSpacing(0)
        workspace.addWidget(self.section_nav)

        self.tabs = SectionStack()
        self.tabs.addTab(self._build_dictate_tab(), "Dictate")
        self.tabs.addTab(self._build_writing_tab(), "Writing tools")
        self.drafts_panel = DraftsPanel(
            self.document_workspace,
            model_provider=self._accepted_writing_model,
            protected_terms_provider=lambda: tuple(self.store.vocabulary_map().values()),
            timeout_provider=lambda: max(
                30.0,
                self.engine.settings.correction_timeout_seconds,
            ),
            inference=self.ai_coordinator,
        )
        self.drafts_panel.stage_private_paste_requested.connect(self._stage_private_text)
        self.tabs.addTab(self.drafts_panel, "Drafts")
        self.document_panel = DocumentEditorPanel(
            self.document_workspace,
            writing_assistant=self.writing_assistant,
            writing_model_provider=self._accepted_writing_model,
            protected_terms_provider=lambda: tuple(self.store.vocabulary_map().values()),
            writing_timeout_provider=lambda: max(
                30.0,
                self.engine.settings.correction_timeout_seconds,
            ),
        )
        self.document_panel.assistant_stage_requested.connect(self._stage_private_text)
        self.tabs.addTab(self.document_panel, "Documents")
        self.tabs.addTab(self._build_vocabulary_tab(), "Vocabulary")
        self.tabs.addTab(self._build_snippets_tab(), "Snippets")
        self.evaluation_tab = self._build_evaluation_tab()
        if self.internal_evaluation:
            self.tabs.addTab(self.evaluation_tab, "Internal evaluation")
        self.tabs.addTab(self._build_recovery_tab(), "Recovery")
        self.tabs.addTab(self._build_history_tab(), "History")
        self.tabs.addTab(self._build_sources_tab(), "Sources")
        for index in range(self.tabs.count()):
            self.section_nav.addItem(self.tabs.tabText(index))
        self.section_nav.currentRowChanged.connect(self.tabs.setCurrentIndex)
        self.tabs.currentChanged.connect(self._sync_section_navigation)
        self.tabs.currentChanged.connect(self._tab_changed)

        self.main_scroll = QScrollArea()
        self.main_scroll.setWidgetResizable(True)
        self.main_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.main_scroll.setWidget(self.tabs)
        workspace.addWidget(self.main_scroll, 1)
        root.addLayout(workspace, 1)
        self.section_nav.setCurrentRow(0)
        self.setCentralWidget(central)

    def _build_global_status_band(self) -> QFrame:
        status = QFrame()
        status.setObjectName("statusBand")
        status_layout = QVBoxLayout(status)
        status_layout.setContentsMargins(14, 10, 14, 10)
        status_layout.setSpacing(6)
        status_row = QHBoxLayout()
        self.state_dot = QFrame()
        self.state_dot.setObjectName("stateDot")
        status_row.addWidget(self.state_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self.status_text = QLabel("Ready")
        self.status_text.setObjectName("statusText")
        self.status_text.setWordWrap(True)
        status_row.addWidget(self.status_text, 1)
        self.private_paste_state = QLabel("Manuscript private paste: empty")
        self.private_paste_state.setObjectName("mutedLabel")
        self.private_paste_state.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.private_paste_state.setWordWrap(True)
        status_row.addWidget(self.private_paste_state)
        self.clear_private_paste_button = QPushButton("Clear")
        self.clear_private_paste_button.setToolTip("Clear Manuscript's memory-only private paste")
        self.clear_private_paste_button.setEnabled(False)
        self.clear_private_paste_button.clicked.connect(self._clear_private_text)
        status_row.addWidget(self.clear_private_paste_button)
        status_layout.addLayout(status_row)
        self.hotkey_label = QLabel()
        self.hotkey_label.setObjectName("mutedLabel")
        self.hotkey_label.setWordWrap(True)
        status_layout.addWidget(self.hotkey_label)
        self.level_bar = QProgressBar()
        self.level_bar.setRange(0, 100)
        self.level_bar.setValue(0)
        self.level_bar.setTextVisible(False)
        self.level_bar.setAccessibleName("Microphone level")
        status_layout.addWidget(self.level_bar)
        return status

    def _build_dictate_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(2, 8, 6, 8)
        layout.setSpacing(10)

        mode_label = QLabel("Writing mode")
        mode_label.setObjectName("sectionTitle")
        layout.addWidget(mode_label)
        mode_hint = QLabel("Choose speed, code formatting, or deeper local cleanup.")
        mode_hint.setObjectName("mutedLabel")
        mode_hint.setWordWrap(True)
        layout.addWidget(mode_hint)
        mode_grid = QGridLayout()
        mode_grid.setHorizontalSpacing(8)
        mode_grid.setVerticalSpacing(8)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_buttons: dict[DictationMode, QPushButton] = {}
        for index, (mode, label, tip) in enumerate((
            (DictationMode.FAST, "Fast", "Lowest delay. Uses local speech and instant cleanup."),
            (
                DictationMode.DEVELOPER,
                "Developer",
                "Formats explicit case, filename, tag, symbol, indentation, and command speech locally.",
            ),
            (DictationMode.REFINE, "Refine", "Inserts fast, then improves text with local AI when available."),
            (DictationMode.ACCURACY, "Accuracy", "Finishes local refinement before inserting text."),
        )):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setProperty("segment", True)
            button.setToolTip(tip)
            button.clicked.connect(lambda _checked=False, value=mode: self._set_mode(value))
            self.mode_group.addButton(button)
            self.mode_buttons[mode] = button
            mode_grid.addWidget(button, index // 2, index % 2)
        self.review_check = QCheckBox("Review before insert")
        self.review_check.setChecked(self.engine.settings.review_before_insert)
        self.review_check.setToolTip("Show raw and corrected text before Manuscript returns to the original app.")
        self.review_check.toggled.connect(self._set_review_mode)
        layout.addLayout(mode_grid)

        self.record_button = QPushButton("Start recording")
        self.record_button.setObjectName("primaryButton")
        self.record_button.setToolTip("Record in Manuscript without pasting into another app")
        self.record_button.clicked.connect(self._toggle_manual_recording)
        recording_row = QGridLayout()
        recording_row.setHorizontalSpacing(8)
        recording_row.setVerticalSpacing(8)
        recording_row.addWidget(self.review_check, 0, 0, 1, 2)
        recording_row.addWidget(self.record_button, 1, 0)
        self.undo_button = QPushButton("Undo last insert")
        self.undo_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowBack))
        self.undo_button.setEnabled(False)
        self.undo_button.clicked.connect(self._undo_last)
        recording_row.addWidget(self.undo_button, 1, 1)
        recording_row.setColumnStretch(0, 1)
        layout.addLayout(recording_row)

        self.result_text = QPlainTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setPlaceholderText("Your recent dictations will appear here.")
        self.result_text.setMinimumHeight(56)
        self.result_text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.result_text.setAccessibleName("Dictation history")
        self.raw_text = QPlainTextEdit()
        self.raw_text.setReadOnly(True)
        self.raw_text.setPlaceholderText("Raw local transcript will appear here.")
        self.raw_text.setAccessibleName("Raw dictation transcript")
        self.diff_text = QPlainTextEdit()
        self.diff_text.setReadOnly(True)
        self.diff_text.setPlaceholderText("Changes will appear here.")
        self.diff_text.setAccessibleName("Dictation changes")
        self.review_tabs = QTabWidget()
        self.review_tabs.setDocumentMode(True)
        self.review_tabs.addTab(self.result_text, "Dictation history")
        self.review_tabs.addTab(self.raw_text, "Raw")
        self.review_tabs.addTab(self.diff_text, "Changes")
        self.review_tabs.setMinimumHeight(112)
        self.review_tabs.setMaximumHeight(220)
        self.review_tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.result_text.textChanged.connect(self._refresh_diff)
        layout.addWidget(self.review_tabs, 1)

        action_band = QFrame()
        action_band.setObjectName("entryBand")
        action_row = QGridLayout(action_band)
        action_row.setContentsMargins(10, 8, 10, 8)
        action_row.setHorizontalSpacing(8)
        action_row.setVerticalSpacing(8)
        self.accept_button = QPushButton("Accept")
        self.accept_button.setEnabled(False)
        self.accept_button.clicked.connect(lambda: self._complete_review("accepted"))
        action_row.addWidget(self.accept_button, 0, 0)
        self.insert_button = QPushButton("Accept + insert")
        self.insert_button.setObjectName("primaryButton")
        self.insert_button.setEnabled(False)
        self.insert_button.clicked.connect(lambda: self._complete_review("inserted"))
        action_row.addWidget(self.insert_button, 0, 1)
        self.copy_button = QPushButton("Stage private paste")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self._copy_latest)
        action_row.addWidget(self.copy_button, 0, 2)
        self.retry_button = QPushButton("Retry")
        self.retry_button.setEnabled(False)
        self.retry_button.clicked.connect(self._retry_review)
        action_row.addWidget(self.retry_button, 1, 0)
        self.reject_button = QPushButton("Reject")
        self.reject_button.setObjectName("dangerButton")
        self.reject_button.setEnabled(False)
        self.reject_button.clicked.connect(lambda: self._complete_review("rejected"))
        action_row.addWidget(self.reject_button, 1, 1)
        self.review_action_band = action_band
        self.review_action_band.hide()
        layout.addWidget(action_band)

        layout.addStretch(1)
        return tab

    def _build_writing_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(10)

        title_row = QHBoxLayout()
        title = QLabel("Local writing tools")
        title.setObjectName("sectionTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)
        self.writing_capture_button = QPushButton("Capture selection")
        self.writing_capture_button.clicked.connect(self._capture_selected_text)
        title_row.addWidget(self.writing_capture_button)
        layout.addLayout(title_row)
        hint = QLabel(
            "Highlight text in another app, then press Ctrl + Shift + Space. "
            "Choose a visible action below. Nothing is sent to the cloud."
        )
        hint.setObjectName("mutedLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.writing_quick_buttons: dict[str, QPushButton] = {}

        controls = QGridLayout()
        controls.setHorizontalSpacing(8)
        controls.setVerticalSpacing(6)
        self.writing_command_label = QLabel("Tone and style")
        self.writing_command_combo = QComboBox()
        self.writing_command_combo.addItems(COMMANDS)
        self.writing_command_label.setBuddy(self.writing_command_combo)
        controls.addWidget(self.writing_command_label, 0, 0)
        controls.addWidget(self.writing_command_combo, 1, 0)
        profile_label = QLabel("Writing context")
        self.writing_profile_combo = QComboBox()
        self.writing_profile_combo.addItems(PROFILES)
        self.writing_profile_combo.setAccessibleName("Writing context")
        profile_label.setBuddy(self.writing_profile_combo)
        controls.addWidget(profile_label, 0, 1)
        controls.addWidget(self.writing_profile_combo, 1, 1)
        language_label = QLabel("Translation language")
        self.writing_language_edit = QLineEdit()
        self.writing_language_edit.setPlaceholderText("Language for Translate")
        language_label.setBuddy(self.writing_language_edit)
        controls.addWidget(language_label, 0, 2)
        controls.addWidget(self.writing_language_edit, 1, 2)
        self.writing_run_button = QPushButton("Run locally")
        self.writing_run_button.setObjectName("primaryButton")
        self.writing_run_button.clicked.connect(self._run_writing_command)
        controls.addWidget(self.writing_run_button, 1, 3)
        layout.addLayout(controls)
        self.writing_instruction_edit = QLineEdit()
        self.writing_instruction_edit.setPlaceholderText("Optional direction, such as: keep the bullet list")
        layout.addWidget(self.writing_instruction_edit)
        analysis_row = QHBoxLayout()
        self.writing_analyze_button = QPushButton("Analyze readability")
        self.writing_analyze_button.clicked.connect(self._analyze_writing)
        analysis_row.addWidget(self.writing_analyze_button)
        analysis_row.addStretch(1)
        layout.addLayout(analysis_row)

        self.writing_source = QPlainTextEdit()
        self.writing_source.setReadOnly(True)
        self.writing_source.setPlaceholderText("Captured text appears here.")
        self.writing_result_edit = QPlainTextEdit()
        self.writing_result_edit.setPlaceholderText("Local result appears here for review.")
        self.writing_analysis = QPlainTextEdit()
        self.writing_analysis.setReadOnly(True)
        self.writing_analysis.setPlaceholderText("Local readability details appear here.")
        self.writing_editors = QTabWidget()
        self.writing_editors.setDocumentMode(True)
        self.writing_editors.addTab(self.writing_source, "Original")
        self.writing_editors.addTab(self.writing_result_edit, "Result - review before replacing")
        self.writing_editors.addTab(self.writing_analysis, "Readability")
        layout.addWidget(self.writing_editors, 1)

        actions = QHBoxLayout()
        self.writing_replace_button = QPushButton("Replace selection")
        self.writing_replace_button.setEnabled(False)
        self.writing_replace_button.clicked.connect(self._replace_selected_text)
        actions.addWidget(self.writing_replace_button)
        self.writing_copy_button = QPushButton("Stage private paste")
        self.writing_copy_button.setEnabled(False)
        self.writing_copy_button.clicked.connect(self._copy_writing_result)
        actions.addWidget(self.writing_copy_button)
        reject = QPushButton("Reject")
        reject.setObjectName("dangerButton")
        reject.clicked.connect(self._reject_writing_result)
        actions.addWidget(reject)
        actions.addStretch(1)
        self.writing_state = QLabel("Waiting for selected text.")
        self.writing_state.setObjectName("mutedLabel")
        self.writing_state.setWordWrap(True)
        layout.addLayout(actions)
        layout.addWidget(self.writing_state)
        return tab

    def _build_vocabulary_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(12)
        title = QLabel("Custom vocabulary")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        description = QLabel("Teach Manuscript how names and special terms should be written.")
        description.setObjectName("mutedLabel")
        description.setWordWrap(True)
        layout.addWidget(description)

        entry = QFrame()
        entry.setObjectName("entryBand")
        entry_layout = QHBoxLayout(entry)
        entry_layout.setContentsMargins(12, 12, 12, 12)
        entry_layout.setSpacing(10)
        self.spoken_edit = QLineEdit()
        self.spoken_edit.setPlaceholderText("What you say")
        self.spoken_edit.setAccessibleName("Spoken form")
        entry_layout.addWidget(self.spoken_edit, 2)
        self.written_edit = QLineEdit()
        self.written_edit.setPlaceholderText("How it should appear")
        self.written_edit.setAccessibleName("Written form")
        entry_layout.addWidget(self.written_edit, 2)
        self.category_combo = QComboBox()
        self.category_combo.setEditable(True)
        self.category_combo.addItems(["custom", "name", "product", "technical"])
        self.category_combo.setAccessibleName("Vocabulary category")
        # An editable combo box exposes its own line edit as a separate control,
        # and that child inherits nothing. A UI sweep of every tab found it as
        # the one control on any page with no accessible name: focus lands
        # there while typing a category and a screen reader announces nothing.
        category_field = self.category_combo.lineEdit()
        if category_field is not None:
            category_field.setAccessibleName("Vocabulary category")
            category_field.setPlaceholderText("custom")
        entry_layout.addWidget(self.category_combo, 1)
        add_button = QPushButton("Add")
        add_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton))
        add_button.clicked.connect(self._add_vocabulary)
        entry_layout.addWidget(add_button)
        layout.addWidget(entry)

        self.vocabulary_table = QTableWidget(0, 3)
        self.vocabulary_table.setHorizontalHeaderLabels(["Spoken form", "Written form", "Category"])
        self.vocabulary_table.setAlternatingRowColors(True)
        self.vocabulary_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.vocabulary_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.vocabulary_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.vocabulary_table.verticalHeader().setVisible(False)
        vocabulary_header = self.vocabulary_table.horizontalHeader()
        vocabulary_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        vocabulary_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        vocabulary_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.vocabulary_table, 1)
        delete_row = QHBoxLayout()
        delete_row.addStretch(1)
        delete_button = QPushButton("Delete selected")
        delete_button.setObjectName("dangerButton")
        delete_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
        delete_button.clicked.connect(self._delete_vocabulary)
        delete_row.addWidget(delete_button)
        layout.addLayout(delete_row)
        return tab

    def _build_history_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(12)
        self.history_sections = QTabWidget()
        self.history_sections.setAccessibleName("Local history sections")

        text_page = QWidget()
        text_layout = QVBoxLayout(text_page)
        text_layout.setContentsMargins(0, 10, 0, 0)
        text_layout.setSpacing(10)
        header_row = QHBoxLayout()
        title = QLabel("Text history")
        title.setObjectName("sectionTitle")
        header_row.addWidget(title)
        header_row.addStretch(1)
        self.history_state = QLabel()
        self.history_state.setObjectName("mutedLabel")
        header_row.addWidget(self.history_state)
        text_layout.addLayout(header_row)

        self.history_table = QTableWidget(0, 4)
        self.history_table.setHorizontalHeaderLabels(["Time", "Mode", "Text", "Delay"])
        self.history_table.setAlternatingRowColors(True)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.verticalHeader().setVisible(False)
        history_header = self.history_table.horizontalHeader()
        history_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        history_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        history_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        history_header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        text_layout.addWidget(self.history_table, 1)
        clear_row = QHBoxLayout()
        clear_row.addStretch(1)
        clear_button = QPushButton("Clear history")
        clear_button.setObjectName("dangerButton")
        clear_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
        clear_button.clicked.connect(self._clear_history)
        clear_row.addWidget(clear_button)
        text_layout.addLayout(clear_row)
        self.history_sections.addTab(text_page, "Text history")

        speech_page = QWidget()
        speech_layout = QVBoxLayout(speech_page)
        speech_layout.setContentsMargins(0, 10, 0, 0)
        speech_layout.setSpacing(8)
        self.speech_log_state = QLabel()
        self.speech_log_state.setObjectName("mutedLabel")
        self.speech_log_state.setWordWrap(True)
        speech_layout.addWidget(self.speech_log_state)

        self.speech_log_table = QTableWidget(0, 5)
        self.speech_log_table.setHorizontalHeaderLabels(
            ["Time", "Mode", "Length", "Period analysis", "Review"]
        )
        self.speech_log_table.setAccessibleName("Encrypted speech improvement logs")
        self.speech_log_table.setAlternatingRowColors(True)
        self.speech_log_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.speech_log_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.speech_log_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.speech_log_table.verticalHeader().setVisible(False)
        self.speech_log_table.setMinimumHeight(105)
        self.speech_log_table.setMaximumHeight(125)
        speech_header = self.speech_log_table.horizontalHeader()
        speech_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        speech_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        speech_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        speech_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        speech_header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.speech_log_table.itemSelectionChanged.connect(self._show_selected_speech_log)
        speech_layout.addWidget(self.speech_log_table)

        self.speech_log_details = QTabWidget()
        self.speech_log_details.setAccessibleName("Speech log transcript stages")
        self.speech_log_details.setMaximumHeight(120)
        self.speech_raw_text = QPlainTextEdit()
        self.speech_raw_text.setReadOnly(True)
        self.speech_raw_text.setAccessibleName("Raw speech transcript")
        self.speech_cleanup_text = QPlainTextEdit()
        self.speech_cleanup_text.setReadOnly(True)
        self.speech_cleanup_text.setAccessibleName("Deterministic cleanup transcript")
        self.speech_final_text = QPlainTextEdit()
        self.speech_final_text.setAccessibleName("Final reviewed transcript")
        self.speech_analysis_text = QPlainTextEdit()
        self.speech_analysis_text.setReadOnly(True)
        self.speech_analysis_text.setAccessibleName("Punctuation analysis")
        for editor in (
            self.speech_raw_text,
            self.speech_cleanup_text,
            self.speech_final_text,
            self.speech_analysis_text,
        ):
            editor.setMaximumHeight(85)
        self.speech_log_details.addTab(self.speech_raw_text, "Raw")
        self.speech_log_details.addTab(self.speech_cleanup_text, "Cleanup")
        self.speech_log_details.addTab(self.speech_final_text, "Final / edit")
        self.speech_log_details.addTab(self.speech_analysis_text, "Period analysis")
        speech_layout.addWidget(self.speech_log_details)

        feedback_row = QHBoxLayout()
        self.speech_punctuation_wrong = QCheckBox("Period placement was wrong")
        self.speech_punctuation_wrong.setAccessibleName("Mark period placement wrong")
        feedback_row.addWidget(self.speech_punctuation_wrong)
        self.speech_notes = QLineEdit()
        self.speech_notes.setPlaceholderText("Optional note for this recording")
        self.speech_notes.setAccessibleName("Speech log note")
        feedback_row.addWidget(self.speech_notes, 1)
        speech_layout.addLayout(feedback_row)

        speech_actions = QHBoxLayout()
        self.speech_play_button = QPushButton("Play")
        self.speech_play_button.clicked.connect(self._play_selected_speech_log)
        speech_actions.addWidget(self.speech_play_button)
        speech_stop_button = QPushButton("Stop")
        speech_stop_button.clicked.connect(stop_audio_playback)
        speech_actions.addWidget(speech_stop_button)
        self.speech_save_button = QPushButton("Save review")
        self.speech_save_button.clicked.connect(self._save_speech_log_review)
        speech_actions.addWidget(self.speech_save_button)
        speech_actions.addStretch(1)
        speech_delete_button = QPushButton("Delete selected")
        speech_delete_button.setObjectName("dangerButton")
        speech_delete_button.clicked.connect(self._delete_selected_speech_log)
        speech_actions.addWidget(speech_delete_button)
        speech_clear_button = QPushButton("Clear logs")
        speech_clear_button.setObjectName("dangerButton")
        speech_clear_button.clicked.connect(self._clear_speech_logs)
        speech_actions.addWidget(speech_clear_button)
        speech_layout.addLayout(speech_actions)
        speech_layout.addStretch(1)
        self.history_sections.addTab(speech_page, "Speech improvement logs")
        layout.addWidget(self.history_sections, 1)
        return tab

    def _build_sources_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(10)
        header = QHBoxLayout()
        title = QLabel("Approved local sources")
        title.setObjectName("sectionTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.source_add_button = QPushButton("Add text files")
        self.source_add_button.setToolTip("Add approved .txt or .md files")
        self.source_add_button.clicked.connect(self._add_local_sources)
        header.addWidget(self.source_add_button)
        self.source_clear_button = QPushButton("Clear sources")
        self.source_clear_button.clicked.connect(self._clear_local_sources)
        header.addWidget(self.source_clear_button)
        layout.addLayout(header)
        description = QLabel(
            "Sources stay in memory only. Manuscript reads only files you choose. No network lookup."
        )
        description.setObjectName("mutedLabel")
        description.setWordWrap(True)
        layout.addWidget(description)
        self.source_state = QLabel("No sources approved.")
        self.source_state.setObjectName("mutedLabel")
        layout.addWidget(self.source_state)

        self.source_tools = QTabWidget()
        self.source_tools.setAccessibleName("Local source tools")
        search_tab = QWidget()
        search_layout = QVBoxLayout(search_tab)
        search_layout.setContentsMargins(0, 10, 0, 0)
        search_layout.setSpacing(10)
        self.source_query = QPlainTextEdit()
        self.source_query.setPlaceholderText("Paste text or use the captured selection.")
        self.source_query.setMaximumHeight(105)
        self.source_query.setAccessibleName("Text for local source search")
        search_layout.addWidget(self.source_query)
        actions = QHBoxLayout()
        use_selection = QPushButton("Use captured text")
        use_selection.clicked.connect(self._use_captured_text_for_sources)
        actions.addWidget(use_selection)
        self.source_search_button = QPushButton("Find citations")
        self.source_search_button.clicked.connect(self._search_local_sources)
        actions.addWidget(self.source_search_button)
        self.source_similarity_button = QPushButton("Check similarity")
        self.source_similarity_button.setAccessibleName("Check local similarity")
        self.source_similarity_button.clicked.connect(self._check_local_similarity)
        actions.addWidget(self.source_similarity_button)
        actions.addStretch(1)
        search_layout.addLayout(actions)
        self.source_results = QPlainTextEdit()
        self.source_results.setReadOnly(True)
        self.source_results.setPlaceholderText("Citations or local similarity results appear here.")
        self.source_results.setAccessibleName("Local source results")
        search_layout.addWidget(self.source_results, 1)
        self.source_tools.addTab(search_tab, "Search + compare")

        chat_tab = QWidget()
        chat_layout = QVBoxLayout(chat_tab)
        chat_layout.setContentsMargins(0, 10, 0, 0)
        chat_layout.setSpacing(10)
        chat_notice = QLabel(
            "Chat uses only the approved files above and local AI. "
            "Questions, excerpts, and answers stay in this session. Nothing is saved or applied."
        )
        chat_notice.setObjectName("mutedLabel")
        chat_notice.setWordWrap(True)
        chat_layout.addWidget(chat_notice)
        question_label = QLabel("Question")
        self.source_chat_question = QPlainTextEdit()
        self.source_chat_question.setPlaceholderText(
            "Ask a question answered by the approved files."
        )
        self.source_chat_question.setMaximumHeight(90)
        self.source_chat_question.setAccessibleName("Question for approved local sources")
        question_label.setBuddy(self.source_chat_question)
        chat_layout.addWidget(question_label)
        chat_layout.addWidget(self.source_chat_question)
        chat_action_row = QHBoxLayout()
        self.source_chat_state = QLabel("No chat answer. Session only.")
        self.source_chat_state.setObjectName("mutedLabel")
        chat_action_row.addWidget(self.source_chat_state, 1)
        self.source_chat_button = QPushButton("Ask approved sources")
        self.source_chat_button.setAccessibleName("Ask approved local sources")
        self.source_chat_button.clicked.connect(self._ask_local_sources)
        chat_action_row.addWidget(self.source_chat_button)
        chat_layout.addLayout(chat_action_row)
        answer_label = QLabel("Local answer and citations")
        self.source_chat_answer = QPlainTextEdit()
        self.source_chat_answer.setReadOnly(True)
        self.source_chat_answer.setPlaceholderText(
            "The local answer and exact approved citations appear here."
        )
        self.source_chat_answer.setAccessibleName("Local source chat answer and citations")
        answer_label.setBuddy(self.source_chat_answer)
        chat_layout.addWidget(answer_label)
        chat_layout.addWidget(self.source_chat_answer, 1)
        self.source_tools.addTab(chat_tab, "Ask sources")
        layout.addWidget(self.source_tools, 1)
        return tab

    def _build_snippets_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(12)

        title = QLabel("Local snippets")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        description = QLabel('Say the trigger by itself, or say "insert snippet" followed by the trigger.')
        description.setObjectName("mutedLabel")
        description.setWordWrap(True)
        layout.addWidget(description)

        self.snippet_trigger_edit = QLineEdit()
        self.snippet_trigger_edit.setPlaceholderText("Spoken trigger, such as email signature")
        self.snippet_trigger_edit.setAccessibleName("Snippet spoken trigger")
        layout.addWidget(self.snippet_trigger_edit)
        self.snippet_content_edit = QPlainTextEdit()
        self.snippet_content_edit.setPlaceholderText("Exact local text to insert")
        self.snippet_content_edit.setMaximumHeight(100)
        self.snippet_content_edit.setAccessibleName("Snippet content")
        layout.addWidget(self.snippet_content_edit)
        add_row = QHBoxLayout()
        add_row.addStretch(1)
        add_button = QPushButton("Save snippet")
        add_button.clicked.connect(self._add_snippet)
        add_row.addWidget(add_button)
        layout.addLayout(add_row)

        self.snippet_table = QTableWidget(0, 2)
        self.snippet_table.setHorizontalHeaderLabels(["Spoken trigger", "Content"])
        self.snippet_table.setAlternatingRowColors(True)
        self.snippet_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.snippet_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.snippet_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.snippet_table.verticalHeader().setVisible(False)
        snippet_header = self.snippet_table.horizontalHeader()
        snippet_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        snippet_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.snippet_table, 1)

        delete_row = QHBoxLayout()
        delete_row.addStretch(1)
        delete_button = QPushButton("Delete selected")
        delete_button.setObjectName("dangerButton")
        delete_button.clicked.connect(self._delete_snippet)
        delete_row.addWidget(delete_button)
        layout.addLayout(delete_row)
        return tab

    def _build_recovery_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(12)
        title = QLabel("Failed text recovery")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        self.recovery_state = QLabel()
        self.recovery_state.setObjectName("mutedLabel")
        self.recovery_state.setWordWrap(True)
        layout.addWidget(self.recovery_state)
        self.recovery_table = QTableWidget(0, 4)
        self.recovery_table.setHorizontalHeaderLabels(["Time", "Status", "Mode", "Text"])
        self.recovery_table.setAlternatingRowColors(True)
        self.recovery_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.recovery_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.recovery_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.recovery_table.verticalHeader().setVisible(False)
        header = self.recovery_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.recovery_table, 1)
        actions = QHBoxLayout()
        copy_button = QPushButton("Stage selected")
        copy_button.clicked.connect(self._copy_recovery)
        actions.addWidget(copy_button)
        actions.addStretch(1)
        delete_button = QPushButton("Delete selected")
        delete_button.clicked.connect(self._delete_recovery)
        actions.addWidget(delete_button)
        clear_button = QPushButton("Clear all")
        clear_button.setObjectName("dangerButton")
        clear_button.clicked.connect(self._clear_recovery)
        actions.addWidget(clear_button)
        layout.addLayout(actions)
        return tab

    def _build_evaluation_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(12)
        header = QHBoxLayout()
        title = QLabel("20-case human evaluation")
        title.setObjectName("sectionTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.evaluation_kind = QComboBox()
        self.evaluation_kind.addItem("Baseline", "baseline")
        self.evaluation_kind.addItem("Final", "final")
        header.addWidget(self.evaluation_kind)
        self.evaluation_start_button = QPushButton("Start / resume")
        self.evaluation_start_button.clicked.connect(self._start_evaluation_run)
        header.addWidget(self.evaluation_start_button)
        layout.addLayout(header)
        self.evaluation_progress = QLabel("No active run")
        self.evaluation_progress.setObjectName("mutedLabel")
        layout.addWidget(self.evaluation_progress)
        instruction = QLabel(
            "Read the fixed phrase aloud. Evaluation does not retain audio unless encrypted speech logs are enabled. Edit only after the local result appears."
        )
        instruction.setObjectName("mutedLabel")
        instruction.setWordWrap(True)
        layout.addWidget(instruction)
        self.evaluation_prompt = QPlainTextEdit()
        self.evaluation_prompt.setReadOnly(True)
        self.evaluation_prompt.setPlaceholderText("Start a run to load case 1.")
        self.evaluation_prompt.setMaximumHeight(78)
        self.evaluation_prompt.setAccessibleName("Capstone phrase to speak")
        layout.addWidget(self.evaluation_prompt)
        self.evaluation_record_button = QPushButton("Start case")
        self.evaluation_record_button.setObjectName("primaryButton")
        self.evaluation_record_button.setEnabled(False)
        self.evaluation_record_button.clicked.connect(self._toggle_evaluation_recording)
        layout.addWidget(self.evaluation_record_button)
        self.evaluation_output = QPlainTextEdit()
        self.evaluation_output.setPlaceholderText("Local result will appear here. You may edit it before accepting.")
        self.evaluation_output.setReadOnly(True)
        self.evaluation_output.setMinimumHeight(105)
        self.evaluation_output.setAccessibleName("Capstone accepted text")
        layout.addWidget(self.evaluation_output, 1)
        self.evaluation_notes = QLineEdit()
        self.evaluation_notes.setPlaceholderText("Optional local note")
        layout.addWidget(self.evaluation_notes)
        actions = QHBoxLayout()
        self.evaluation_accept_button = QPushButton("Accept")
        self.evaluation_accept_button.clicked.connect(
            lambda: self._complete_evaluation_case("accepted")
        )
        actions.addWidget(self.evaluation_accept_button)
        self.evaluation_retry_button = QPushButton("Retry")
        self.evaluation_retry_button.clicked.connect(
            lambda: self._complete_evaluation_case("retry")
        )
        actions.addWidget(self.evaluation_retry_button)
        self.evaluation_reject_button = QPushButton("Reject")
        self.evaluation_reject_button.setObjectName("dangerButton")
        self.evaluation_reject_button.clicked.connect(
            lambda: self._complete_evaluation_case("rejected")
        )
        actions.addWidget(self.evaluation_reject_button)
        actions.addStretch(1)
        self.evaluation_export_button = QPushButton("Export results")
        self.evaluation_export_button.clicked.connect(self._export_evaluation)
        actions.addWidget(self.evaluation_export_button)
        layout.addLayout(actions)
        self._set_evaluation_actions(False, retry=False)
        return tab

    def _configure_accessibility(self) -> None:
        set_accessible(
            self,
            "Manuscript main window",
            "Local voice dictation, selected-text tools, documents, recovery, and sources.",
        )
        set_accessible(
            self.tabs,
            "Manuscript sections",
            "The page selected from the section navigation.",
        )
        set_accessible(
            self.section_nav,
            "Manuscript section navigation",
            "Use Up and Down Arrow to move between all Manuscript sections.",
        )
        set_accessible(
            self.main_scroll,
            "Manuscript content",
            "Scroll vertically when display scaling leaves less room.",
        )
        set_accessible(self.settings_button, "Manuscript settings")
        set_accessible(
            self.status_text,
            "Dictation status",
            self.status_text.text(),
        )
        set_accessible(
            self.hotkey_label,
            "Dictation keyboard shortcuts",
            "The active dictation, hands-free, and Manuscript private paste shortcuts.",
        )
        set_accessible(
            self.level_bar,
            "Microphone level",
            "Live microphone input level from 0 to 100.",
        )
        for mode, button in self.mode_buttons.items():
            set_accessible(button, f"{button.text()} writing mode", button.toolTip())
        set_accessible(
            self.record_button,
            "Start or stop recording",
            "Records in Manuscript without inserting into another application.",
        )
        set_accessible(self.review_tabs, "Dictation review text")
        set_accessible(
            self.copy_button,
            "Stage dictation for Manuscript private paste",
            "Stores text in memory only. Does not use the Windows clipboard.",
        )
        set_accessible(
            self.private_paste_state,
            "Manuscript private paste status",
            self.private_paste_state.text(),
        )
        set_accessible(
            self.clear_private_paste_button,
            "Clear Manuscript private paste",
            "Removes the staged memory-only text.",
        )

        set_accessible(self.writing_command_combo, "Selected-text command")
        set_accessible(self.writing_language_edit, "Translation language")
        set_accessible(self.writing_instruction_edit, "Optional writing direction")
        set_accessible(self.writing_source, "Captured original text")
        set_accessible(self.writing_result_edit, "Selected-text command result")
        set_accessible(self.writing_analysis, "Readability analysis")
        set_accessible(self.writing_editors, "Selected-text review")
        set_accessible(self.writing_state, "Selected-text status", self.writing_state.text())
        set_accessible(self.drafts_panel, "Local draft generation")
        set_accessible(self.document_panel, "Local document workspace")

        set_accessible(self.vocabulary_table, "Custom vocabulary entries")
        set_accessible(self.snippet_table, "Saved local snippets")
        set_accessible(self.history_table, "Local dictation history")
        set_accessible(self.recovery_table, "Failed text recovery items")
        set_accessible(self.source_state, "Local source status", self.source_state.text())
        set_accessible(
            self.source_chat_state,
            "Local source chat status",
            self.source_chat_state.text(),
        )
        set_accessible(
            self.source_chat_button,
            "Ask approved local sources",
            "Uses local AI. Nothing is saved or applied.",
        )
        set_accessible(self.evaluation_kind, "Evaluation run type")
        set_accessible(
            self.evaluation_progress,
            "Capstone evaluation progress",
            self.evaluation_progress.text(),
        )
        set_accessible(self.evaluation_notes, "Optional capstone case note")
        set_accessible(self.recovery_state, "Recovery status", self.recovery_state.text())

    def _connect_bridge(self) -> None:
        self.bridge.session_event.connect(self._handle_session_event)
        self.bridge.level_changed.connect(self._handle_level)
        self.bridge.hotkey_pressed.connect(self._hotkey_press)
        self.bridge.hotkey_released.connect(self._hotkey_release)
        self.bridge.hands_free_pressed.connect(self._toggle_hands_free_recording)
        self.bridge.hands_free_cancel.connect(self._cancel_hands_free_recording)
        self.bridge.writing_command_pressed.connect(self._capture_selected_text_for_palette)
        self.bridge.automatic_selection_detected.connect(self._handle_automatic_selection)
        self.bridge.automatic_selection_cleared.connect(
            self._handle_automatic_selection_cleared
        )
        self.bridge.private_paste_pressed.connect(self._paste_private_text)
        self.bridge.writing_result.connect(self._handle_writing_result)
        self.bridge.palette_writing_result.connect(self._handle_palette_writing_result)
        self.bridge.palette_sentence_result.connect(self._handle_palette_sentence_result)
        self.bridge.palette_sentence_progress.connect(
            self._handle_palette_sentence_progress
        )
        self.bridge.source_report.connect(self._handle_source_report)
        self.bridge.source_chat_result.connect(self._handle_source_chat_result)
        self.bridge.hotkey_error.connect(self._show_hotkey_error)

    def _capture_selected_text(self) -> None:
        self._capture_selected_text_for_review(show_palette=False)

    def _capture_selected_text_for_palette(self) -> None:
        self._capture_selected_text_for_review(show_palette=True)

    def _read_document_context(self, target_window) -> str:
        """Read the document being dictated into, for name evidence only.

        Called by the engine on its own thread the moment recording starts, so
        the UI Automation cost is spent while the writer is still speaking
        rather than between the last word and the text appearing. That ordering
        is the point: a read on the dictation path cost 0.5 seconds and delayed
        the activation cue on 2026-07-29.

        The text never leaves this machine and is never sent to the model. It
        is used only to decide how a name the writer already uses is spelled.
        """
        if not sys.platform.startswith("win"):
            return ""
        handle = int(getattr(target_window, "handle", 0) or 0)
        if handle <= 0:
            return ""
        reader = WindowsSelectionReader(max_characters=DOCUMENT_CONTEXT_CHARACTERS)
        context_factory = getattr(reader, "automation_thread_context", None)
        context = context_factory() if callable(context_factory) else nullcontext()
        with context:
            detected = reader.read_document()
        record = getattr(detected, "record", None)
        return str(getattr(record, "text", "") or "")

    def _capture_selected_text_for_review(self, *, show_palette: bool) -> None:
        logger.info("Selected-text hotkey/capture requested")
        if (
            show_palette
            and self.selected_text_record is not None
            and self.rewrite_palette.isVisible()
            and self.rewrite_palette.view == "badge"
        ):
            current = active_window()
            captured = self.selected_text_record.window
            if (
                current.handle == captured.handle
                and current.process_id == captured.process_id
            ):
                logger.info("Opening existing Writing HUD capture without rereading source")
                self._pause_selection_monitor()
                self.rewrite_palette.open_current_capture()
                return
        if show_palette:
            self._pause_selection_monitor()
        record = None
        capture_error: RuntimeError | None = None
        if show_palette:
            reader = WindowsSelectionReader(
                max_characters=500_000,
                include_caret_sentence=True,
                # The automatic badge has always asked for Word scope widening
                # and the shortcut never did, so the same caret in the same
                # document produced a whole review one way and "No selected text
                # was found" the other. In Word a caret with no selection now
                # means the whole document, which is what a writer pressing
                # "check my writing" is asking for.
                include_word_page=True,
            )
            detected = reader.read()
            if detected is not None:
                detected = self._expand_detected_to_text_box(detected, reader=reader)
            else:
                detected = reader.read_document()
            if detected is not None:
                record = detected.record
        if record is None:
            try:
                record = self.engine.inserter.capture_selection()
            except RuntimeError as exc:
                capture_error = exc
        if (
            record is None
            and show_palette
            and self.selected_text_record is not None
            and self.rewrite_palette.isVisible()
        ):
            # The badge prepares a whole-document review the moment the caret
            # lands, and the reuse branch above only fires while the palette is
            # still showing the badge. Once it had moved to the card, pressing
            # the shortcut re-read the source, found a bare caret rather than a
            # selection, and called require_recapture - which threw away a
            # review that was already built and left the writer looking at the
            # first section with Replace greyed out and nothing advancing.
            #
            # A shortcut press is a request to review this document, never a
            # request to discard the review of it that is already on screen.
            current = active_window()
            captured = self.selected_text_record.window
            if (
                current.handle == captured.handle
                and current.process_id == captured.process_id
            ):
                logger.info(
                    "Writing HUD capture found no new selection; keeping the "
                    "review already open for this document"
                )
                self.rewrite_palette.open_current_capture()
                return
        if record is None:
            exc = capture_error or RuntimeError(
                "No editable text was found. Put the cursor in a document and try again."
            )
            if show_palette:
                logger.warning("Writing HUD capture failed: %s", exc)
                self._palette_request_id += 1
                self.selected_text_record = None
                self.rewrite_palette.require_recapture(str(exc))
                return
            self._show_from_tray()
            self.tabs.setCurrentIndex(1)
            update_status_label(self.writing_state, str(exc), assertive=True)
            return
        # Name the document, on this path too. The automatic monitor logs
        # "editable focus detected title=..." and the shortcut logged nothing
        # carrying a title, so the app's log could say a review was built and
        # never say what it was built from. That is not cosmetic: on
        # 2026-09-08 the batch harness scored a 20 essay run a crash on 8
        # errors and 2 dead, and every one of the 5 failures still traceable
        # in the un-rotated log was a document this app had captured
        # correctly - essay 10 built a review of 22 sections at 9005
        # characters, which is exactly essay 10, and then waited five minutes
        # for a line this path never wrote. The two paths race, the shortcut
        # wins whenever the monitor has not settled within about 16 seconds of
        # the document opening, and only one of them was visible from
        # outside.
        logger.info(
            "Writing HUD shortcut capture prepared title=%r scope=%s chars=%d",
            record.window.title,
            record.scope,
            len(record.text),
        )
        self.selected_text_record = record
        self.writing_source.setPlainText(record.text)
        self.writing_result_edit.clear()
        self.writing_analysis.clear()
        self.writing_replace_button.setEnabled(False)
        self.writing_copy_button.setEnabled(False)
        update_status_label(self.writing_state, "Captured. Choose a command.")
        if show_palette:
            self._palette_request_id += 1
            self.rewrite_palette.show_capture(
                record.text,
                document_scope=record.scope == "document",
            )
        else:
            self._show_from_tray()
            self.tabs.setCurrentIndex(1)

    def _handle_automatic_selection(self, detected: object) -> None:
        if not isinstance(detected, DetectedSelection):
            return
        if isinstance(self.selection_monitor, WindowsSelectionMonitor):
            current = active_window()
            if (
                current.handle != detected.record.window.handle
                or current.process_id != detected.record.window.process_id
            ):
                logger.info(
                    "Writing HUD ignored stale focus signal title=%r",
                    detected.record.window.title,
                )
                return
        logger.info(
            "Writing HUD focus signal received title=%r chars=%d",
            detected.record.window.title,
            len(detected.record.text),
        )
        # The writer is in a document, so the HUD belongs in front of it.
        self.rewrite_palette.set_stays_on_top(True)
        palette_visible = self.rewrite_palette.isVisible()
        if (
            not self.engine.settings.automatic_writing_hud
            or self._quitting
            or self.engine.is_busy
            or time.monotonic() < self._automatic_hud_suppress_until
            or (
                palette_visible
                and (
                    self.rewrite_palette.view != "badge"
                    or not self.rewrite_palette.automatic
                )
                # ...but only while the review on screen still has a document
                # behind it. A card whose source window is gone blocked every
                # later focus signal, so the next document opened into the
                # previous one's review: an eleven-section job was served to a
                # ten-section document and the counter read "4 of 11" for text
                # it had never seen.
                #
                # It briefly also asked whether the focused window WAS the
                # review's window, letting any other window through so the
                # writer could move to a second document. That is the right
                # answer for a second document and the wrong one for every
                # other window on the machine. Measured 2026-08-15: in ten
                # minutes the HUD captured the chat application eleven times
                # and the essay it was correcting twice, rebuilding the review
                # on each one, which is what a writer sees as the card
                # spamming them every time they click.
                #
                # A focus signal is the machine noticing, not the writer
                # asking. It may not take a review off the screen. Moving to a
                # second document is served by the writer pressing the
                # shortcut, which reads a fresh capture and replaces the
                # review, and by dismissing the card they are looking at.
                and self._hud_source_window_alive()
                # ...unless the writer has opened a different document. The
                # version that let ANY other window through was reverted for
                # good reason: in ten minutes it captured the chat application
                # eleven times and the essay twice. This is narrower, and this
                # log is the evidence for why it holds. Every spurious capture
                # that day was scope="selection" - a chat box, a browser field,
                # a search box - and every real document was scope="document"
                # from a different top-level window. So that is the test, and
                # a review is still never torn down by a click inside a text
                # field somewhere else on the machine.
                and not self._is_a_different_document(detected.record)
            )
        ):
            # Say which condition refused, always. This return was silent, and
            # on 2026-08-18 that cost a whole investigation: Word was detected
            # at 5013 characters, "focus signal received" was logged, and then
            # nothing at all appeared - no capture, no badge, no reason. From
            # the outside it is indistinguishable from the focus watcher never
            # firing, and there are five separate conditions here that all look
            # the same in the log. A refusal the writer can see on screen has
            # to be a refusal the log can name.
            logger.info(
                "Writing HUD ignored a focus signal title=%r chars=%d reason=%s",
                detected.record.window.title,
                len(detected.record.text),
                self._automatic_hud_refusal_reason(palette_visible),
            )
            return
        current_record = self.selected_text_record
        incoming_record = detected.record
        if (
            palette_visible
            and self.rewrite_palette.automatic
            and self.rewrite_palette.view == "badge"
            and current_record is not None
            and current_record.scope == "document"
            and incoming_record.scope == "selection"
            and current_record.window.handle == incoming_record.window.handle
            and current_record.window.process_id == incoming_record.window.process_id
            and incoming_record.text in current_record.text
            and (
                (
                    bool(current_record.document_digest)
                    and current_record.document_digest == incoming_record.document_digest
                )
                or (
                    bool(current_record.control_runtime_id)
                    and current_record.control_runtime_id
                    == incoming_record.control_runtime_id
                )
            )
        ):
            # Word exposes the full document first, then reports the temporary
            # range Manuscript marks for the first review. Keep the document
            # capture; replacing it here collapses an N-part job into 1 of 1.
            logger.info(
                "Writing HUD kept document capture; ignored marked range chars=%d",
                len(incoming_record.text),
            )
            return
        record = detected.record
        # A document that has already been through a complete review must not
        # be offered back. Essay 1 on 2026-08-15 finished all 22 sections at
        # 23:58:51 and the badge re-fired on the same text nine seconds later,
        # rebuilding the whole review: "sections=22 atomic=0 characters=9555".
        # The writer was then walked through 22 sections with nothing in them,
        # which is what they reported as "no corrections made to it".
        #
        # The obvious-looking fix does not work and was checked before this
        # one was written: the manual's "No Issues, collapse" cannot catch it,
        # because a review builds one section per span whether or not there is
        # anything to correct, so a finished document produces 22 sections and
        # never reaches the empty card at all. Measured on a clean 7 paragraph
        # document: atomic=0, sections=7, counter "1 of 7".
        #
        # This is keyed on the text and not on a timer, so a document the
        # writer leaves alone is never re-offered however long they look at it,
        # and one they edit is offered again immediately because the text no
        # longer matches. The shortcut is unaffected: this is the machine
        # noticing, not the writer asking.
        #
        # What is withheld is the review, not the badge. Withholding the badge
        # too was the first version of this and it left a reviewed document
        # with no way back in: on 2026-08-18 Word was detected at 5013
        # characters, this line logged, and nothing appeared on screen at all.
        # show_capture defers the analysis instead, and the click finishes it.
        already_reviewed = _normalized_document_text(record.text) in self._reviewed_texts
        if already_reviewed:
            logger.info(
                "Writing HUD will not re-offer a document already reviewed; "
                "badge stays for the writer scope=%s chars=%d",
                record.scope,
                len(record.text),
            )
        logger.info(
            "Writing HUD capture prepared scope=%s chars=%d",
            record.scope,
            len(record.text),
        )
        # UI Automation can report the same focused range on every poll. Do
        # not rebuild the badge for that duplicate signal: replacing its page
        # between mouse-down and mouse-up cancels a real click on Windows.
        unchanged = _is_the_same_capture(self.selected_text_record, record)
        if self._badge_clear_timer is not None and unchanged:
            # The capture that was about to be cleared came straight back
            # (Word focus flap); keep the existing badge untouched.
            self._badge_clear_timer.stop()
            self._badge_clear_pending_record = None
        if (
            palette_visible
            and self.rewrite_palette.automatic
            and self.rewrite_palette.view == "badge"
            and unchanged
        ):
            # The caret may have moved even though nothing was typed, so the
            # record is refreshed WITHOUT rebuilding the review. The badge then
            # still follows the caret, which is the only thing the anchor is
            # for.
            self.selected_text_record = record
            return
        self.selected_text_record = record
        self.writing_source.setPlainText(record.text)
        self.writing_result_edit.clear()
        self.writing_analysis.clear()
        self.writing_replace_button.setEnabled(False)
        self.writing_copy_button.setEnabled(False)
        update_status_label(
            self.writing_state,
            "Selected text detected automatically. Review it in the Writing HUD.",
        )
        self._palette_request_id += 1
        self.rewrite_palette.show_capture(
            record.text,
            automatic=True,
            anchor=detected.anchor,
            document_scope=record.scope == "document",
            reviewed_already=already_reviewed,
        )
        logger.info(
            "Writing HUD badge shown visible=%s position=(%d,%d)",
            self.rewrite_palette.isVisible(),
            self.rewrite_palette.x(),
            self.rewrite_palette.y(),
        )

    def _is_a_different_document(self, incoming) -> bool:
        """Is this capture a whole document in a window we are not reviewing?"""
        if incoming.scope != "document":
            return False
        record = self.selected_text_record
        if record is None:
            return True
        return (
            incoming.window.handle != record.window.handle
            or incoming.window.process_id != record.window.process_id
        )

    def _automatic_hud_refusal_reason(self, palette_visible: bool) -> str:
        """Name every condition currently refusing an automatic capture.

        All of them, not the first: two were true at once on 2026-08-18 and
        reporting only one would have sent the next reader after the wrong one.
        """
        reasons: list[str] = []
        if not self.engine.settings.automatic_writing_hud:
            reasons.append("automatic-hud-off")
        if self._quitting:
            reasons.append("quitting")
        if self.engine.is_busy:
            reasons.append("engine-busy")
        if time.monotonic() < self._automatic_hud_suppress_until:
            reasons.append("suppressed")
        if palette_visible:
            if self.rewrite_palette.view != "badge":
                reasons.append(f"live-review-open:{self.rewrite_palette.view}")
            if not self.rewrite_palette.automatic:
                reasons.append("writer-opened-review")
        return ",".join(reasons) or "unknown"

    def _hud_source_window_alive(self) -> bool:
        """Does the review currently on screen still have a source document?

        Only a real, observable dead window counts. Without a record, without a
        handle, or if the check itself fails, say yes - that keeps the existing
        behaviour and never discards a live review on a guess.
        """
        record = self.selected_text_record
        if record is None:
            return True
        handle = int(getattr(record.window, "handle", 0) or 0)
        if handle <= 0:
            return True
        try:
            return bool(ctypes.windll.user32.IsWindow(handle))
        except Exception:  # noqa: BLE001 - never drop a review on a failed probe
            logger.debug("Writing HUD source window probe failed", exc_info=True)
            return True

    def _close_hud_if_source_gone(self) -> None:
        record = self.selected_text_record
        if not self.rewrite_palette.isVisible():
            self._hud_source_was_alive = False
            self._hud_orphan_ticks = 0
            return
        if record is None:
            self._hud_source_was_alive = False
            # An error card that lost its record ("capture it again") has no
            # source to watch and nothing that will ever close it, yet it
            # holds keyboard focus for the whole desktop. Give the user a
            # short window to read it, then close it.
            if (
                self.rewrite_palette.automatic
                and self.rewrite_palette.view != "badge"
                and not self.rewrite_palette._busy
            ):
                self._hud_orphan_ticks = getattr(self, "_hud_orphan_ticks", 0) + 1
                if self._hud_orphan_ticks >= 10:
                    self._hud_orphan_ticks = 0
                    logger.info(
                        "Writing HUD closed because its capture is gone and "
                        "no source window remains to watch",
                    )
                    self.rewrite_palette.finish_choice()
                    self._resume_selection_monitor()
            return
        self._hud_orphan_ticks = 0
        handle = int(getattr(record.window, "handle", 0) or 0)
        if handle <= 0:
            return
        alive = bool(
            ctypes.windll.user32.IsWindow(handle)
            and ctypes.windll.user32.IsWindowVisible(handle)
        )
        if alive:
            self._hud_source_was_alive = True
            return
        # Only a source that was actually observed alive counts as
        # "disappeared"; synthetic records never close the HUD.
        if not getattr(self, "_hud_source_was_alive", False):
            return
        self._hud_source_was_alive = False
        logger.info(
            "Writing HUD closed because its source window %d disappeared",
            handle,
        )
        self._badge_clear_pending_record = None
        if self._badge_clear_timer is not None:
            self._badge_clear_timer.stop()
        self._palette_request_id += 1
        self.selected_text_record = None
        self._clear_palette_review_state()
        self.rewrite_palette.finish_choice()
        self._resume_selection_monitor()

    def _handle_automatic_selection_cleared(self) -> None:
        self.rewrite_palette.set_target_marking_enabled(False)
        self.target_underline.clear()
        # Decided BEFORE the early returns below, which is the whole fix. Those
        # returns require the badge view, so with a review card open a focus
        # change never reached this and the card stayed in front of everything
        # the writer clicked. Reported 2026-08-17: "remains on top even when
        # another area is clicked".
        #
        # A pinned HUD is exempt: set_stays_on_top refuses to drop it, because
        # pinning is the writer asking for exactly that.
        self._release_hud_top_if_focus_left()
        # Mouse-down on the badge pauses the monitor before the source editor
        # loses focus. A cleared signal may already be queued on the Qt thread;
        # do not let that stale signal close the badge before mouse-up lands.
        if bool(getattr(self.selection_monitor, "paused", False)):
            return
        if (
            not self.rewrite_palette.isVisible()
            or not self.rewrite_palette.automatic
            or self.rewrite_palette.view != "badge"
        ):
            return
        record = self.selected_text_record
        focused = active_window()
        if (
            record is not None
            and focused.handle > 0
            and (
                focused.handle != record.window.handle
                or focused.process_id != record.window.process_id
            )
        ):
            # A different top-level window is an exact focus loss, not Word's
            # transient UI Automation flap. Remove the icon immediately.
            #
            self._badge_clear_pending_record = record
            self._finish_badge_clear()
            return
        # Word's UI Automation flaps focus once per poll while the caret sits
        # still, which made the badge blink and re-request AI every second.
        # Give the same capture a grace window to re-detect before hiding.
        if self._badge_clear_timer is None:
            self._badge_clear_timer = QTimer(self)
            self._badge_clear_timer.setSingleShot(True)
            self._badge_clear_timer.setInterval(1_500)
            self._badge_clear_timer.timeout.connect(self._finish_badge_clear)
        self._badge_clear_pending_record = self.selected_text_record
        self._badge_clear_timer.start()

    def _release_hud_top_if_focus_left(self) -> None:
        """Let other windows come in front once the writer leaves the document.

        The HUD's own window is not a focus loss - clicking a button on the card
        activates it - and neither is the document under review. Anything else
        is the writer choosing another window.
        """
        palette = self.rewrite_palette
        if not palette.isVisible() or palette.pinned:
            return
        focused = active_window()
        if focused.handle <= 0:
            return
        if int(focused.handle) == int(palette.winId()):
            return
        record = self.selected_text_record
        if record is None:
            # Not knowing which document is under review is not the same as
            # the writer having left it, and it must not be treated as such.
            #
            # This used to run only from the selection monitor's cleared
            # callback, where a missing record really did mean the capture was
            # gone. d6dae27 put it on the 175ms focus timer to fix a HUD that
            # could never be lowered during a review, and that made a
            # momentary gap between captures enough to drop the card behind
            # Word - which the owner reported as the suggestion window sitting
            # behind the document while it worked.
            #
            # A review that is still preparing is exactly when the writer needs
            # to see it, so the safe answer to "which document is this?" being
            # unknown is to change nothing.
            return
        if (
            focused.handle == record.window.handle
            and focused.process_id == record.window.process_id
        ):
            return
        palette.set_stays_on_top(False)
        # The writer has moved to some other window, so the reason the monitor
        # was paused is gone. It is paused on badge mouse-down to stop the
        # capture churn while a review is being worked on, and it was only ever
        # resumed when that review FINISHED. A writer who leaves a section
        # unfinished and opens the next document left it paused forever: on
        # 2026-08-18 the last focus detection in the whole log was 16:35:18,
        # the review sat at 9 of 10, and every window after that produced no
        # signal at all. Not a refusal - nothing. The app was deaf, which is
        # what "the icon will not appear" was on the second document.
        self._resume_selection_monitor()

    def _finish_badge_clear(self) -> None:
        if self._badge_clear_pending_record is None:
            return
        if self._badge_clear_pending_record != self.selected_text_record:
            # A newer capture replaced the pending one; nothing to clear.
            self._badge_clear_pending_record = None
            return
        if bool(getattr(self.selection_monitor, "paused", False)):
            self._badge_clear_pending_record = None
            return
        if (
            not self.rewrite_palette.isVisible()
            or not self.rewrite_palette.automatic
            or self.rewrite_palette.view != "badge"
        ):
            self._badge_clear_pending_record = None
            return
        self._badge_clear_pending_record = None
        self._palette_request_id += 1
        self.selected_text_record = None
        self.rewrite_palette.finish_choice()

    def _expand_detected_to_text_box(
        self,
        detected: DetectedSelection,
        *,
        reader: WindowsSelectionReader | None = None,
    ) -> DetectedSelection:
        """Keep one HUD session alive across the entire active text box."""
        if detected.record.scope == "document":
            return detected
        if not (
            detected.record.document_digest
            or detected.record.control_runtime_id
        ):
            return detected
        source_reader = reader or WindowsSelectionReader(max_characters=500_000)
        document = source_reader.read_document()
        if document is None:
            # A transient UI Automation blackout here silently shrank the
            # session to one caret sentence; one retry restores the page.
            time.sleep(0.15)
            document = source_reader.read_document()
        if document is None:
            logger.warning(
                "Writing HUD document expansion failed; keeping %d-char selection",
                len(detected.record.text),
            )
            return detected
        if document.record.window.handle != detected.record.window.handle:
            return detected
        if detected.record.text not in document.record.text:
            return detected
        return DetectedSelection(document.record, detected.anchor)

    def _read_current_writing_record(self, scope: str):
        reader = WindowsSelectionReader(max_characters=500_000)
        detected = reader.read_document() if scope == "document" else reader.read()
        return detected.record if detected is not None else None

    def _restore_writing_selection(self, record) -> bool:
        return WindowsSelectionReader(max_characters=500_000).restore_selection(record)

    def _capture_entire_document_for_palette(self) -> None:
        current = self.selected_text_record
        if current is None:
            self.rewrite_palette.show_error(
                "Put the cursor in a document, then press the Writing HUD hotkey again."
            )
            return
        if not self.engine.inserter.activate_window(current.window):
            self.rewrite_palette.show_error("Manuscript could not return to the source document.")
            return
        reader = WindowsSelectionReader(max_characters=500_000)
        detected = reader.read_document()
        if detected is None or detected.record.window.handle != current.window.handle:
            self.rewrite_palette.show_error(
                "This editor did not expose its full document through Windows accessibility. "
                "Use Ctrl+A, then open the Writing HUD."
            )
            return
        record = detected.record
        self.selected_text_record = record
        self.writing_source.setPlainText(record.text)
        self.writing_result_edit.clear()
        self._palette_request_id += 1
        self.rewrite_palette.show_capture(
            record.text,
            automatic=False,
            anchor=detected.anchor,
            document_scope=True,
        )
        update_status_label(
            self.writing_state,
            "Entire document loaded. Review each completed section in order.",
        )

    def _analyze_writing(self) -> None:
        source = self.writing_source.toPlainText().strip()
        try:
            report = analyze_readability(source)
        except ValueError as exc:
            update_status_label(self.writing_state, str(exc), assertive=True)
            return
        self.writing_analysis.setPlainText(report.as_text())
        self.writing_editors.setCurrentWidget(self.writing_analysis)
        update_status_label(
            self.writing_state,
            f"Readability: {report.score}/100 ({report.label}).",
        )

    def _warm_adaptive_polish(self) -> bool:
        token = self.ai_coordinator.begin_update()
        try:
            readiness = self.polish_engine.warm()
            snapshot = self.polish_engine.hardware_provider()
            catalog = self.polish_engine.packs.catalog(refresh_installed=True)
            installed_catalog = tuple(
                pack
                for pack in catalog
                if bool(getattr(pack, "installed", False))
                and bool(getattr(pack, "validated", False))
            )
            preferred_pack_id = self.polish_engine.packs.migrate_pack_id(
                self.engine.settings.polish_model_pack_id
            )
            decision = ClassifierDecision(
                needs_llm=True,
                error_categories=("first-launch-probe",),
                severity=1.0,
                context_scope=ContextScope.SENTENCE,
                english_confidence=1.0,
                backend="startup",
                elapsed_ms=0.0,
            )
            plan = self.polish_engine.policy.plan(
                snapshot,
                PolishTask.PROOFREAD,
                PolishMode.AUTO,
                classifier=decision,
                packs=installed_catalog,
                preferred_pack_id=preferred_pack_id,
            )
            logger.info(
                "Adaptive Polish ready dictionary=%s classifier=%s pack=%s backend=%s",
                readiness.get("dictionary"),
                readiness.get("classifier"),
                plan.pack_id or "rules-only",
                plan.backend.value,
            )
            if not plan.use_llm or not plan.pack_id:
                # Say why, at WARNING. Coming up rules-only turns the product's
                # main feature off, and it was only ever visible as one INFO
                # line reading "pack=rules-only" - which is how a run that
                # corrected nothing was investigated as a capture fault for an
                # hour. The reason is usually momentary (another program holding
                # the GPU at launch) and every later request re-plans, so this
                # is a warning about the warm-up, not a permanent state.
                reason = (
                    plan.downgrade_reason
                    or plan.reason
                    or "no installed pack fits this hardware"
                )
                logger.warning("Adaptive Polish came up rules-only: %s", reason)
                # Laundered, because downgrade_reason can name the pack or the
                # runtime, and those names are kept out of user-facing text.
                self._writing_ai_reason = (
                    user_ai_status(reason)
                    or "Local AI is not available. Corrections use built-in rules."
                )
            # Load the selected model now, in this startup daemon thread, so
            # the user's first correction does not pay the multi-second cold
            # load. The acceptance probe also records a passing benchmark, so
            # the first request skips its own probe entirely.
            if plan.use_llm and plan.pack_id:
                pack = next(
                    (
                        value
                        for value in installed_catalog
                        if value.pack_id == plan.pack_id
                    ),
                    None,
                )
                if pack is not None:
                    prepare = getattr(self.polish_engine.runtime, "prepare", None)
                    if callable(prepare) and not bool(
                        prepare(
                            plan,
                            consent=self.engine.settings.polish_download_consent,
                        )
                    ):
                        # A missing bundled host is not a failed model. The
                        # adaptive runtime still has its verified compatibility
                        # backend, which must receive the normal acceptance probe.
                        logger.info(
                            "Bundled local AI preparation failed; probing fallback"
                        )
                    warmed = self.polish_engine._accept_plan(
                        plan,
                        snapshot,
                        pack,
                        allow_installed_fallback=False,
                    )
                    loaded = False
                    if warmed.use_llm:
                        # A cached passing benchmark skips the probe, which
                        # also skips the model load. Warm it before sharing
                        # the accepted plan with dictation and other tools.
                        runtime_status = self.polish_engine.runtime.status(
                            warmed.model, timeout=2.0
                        )
                        generation = None
                        if not runtime_status.loaded:
                            generation = self.polish_engine.runtime.generate(
                                GenerationRequest(
                                    text="Warm start.",
                                    command="Proofread",
                                    profile="General",
                                    instruction=(
                                        "Return the text unchanged. This is a "
                                        "model warm-up request."
                                    ),
                                    language="",
                                    protected_terms=(),
                                    plan=dataclass_replace(
                                        warmed,
                                        max_output_tokens=32,
                                        context_window=min(
                                            2048, warmed.context_window or 2048
                                        ),
                                    ),
                                )
                            )
                            runtime_status = self.polish_engine.runtime.status(
                                generation.model, timeout=2.0
                            )
                        expected_model = (
                            str(getattr(generation, "model", "") or "").strip()
                            or warmed.model
                        )
                        expected_backend = getattr(
                            generation,
                            "backend",
                            warmed.backend,
                        )
                        if not _runtime_status_matches_plan(
                            runtime_status,
                            warmed,
                            expected_model=expected_model,
                            expected_backend=expected_backend,
                        ):
                            raise RuntimeError(
                                "Accepted local AI failed exact runtime verification."
                            )
                        loaded = True
                        published = self.ai_coordinator.accept_verified(
                            warmed,
                            token=token,
                            actual_model=expected_model,
                            actual_backend=expected_backend,
                        )
                    else:
                        published = self.ai_coordinator.clear(token=token)
                    lease = self.ai_coordinator.lease() if published else None
                    if lease is not None:
                        self._installed_writing_models.add(lease.plan.model)
                    logger.info(
                        "Adaptive Polish model prewarmed pack=%s use_llm=%s "
                        "loaded=%s",
                        warmed.pack_id,
                        warmed.use_llm,
                        loaded,
                    )
                    if loaded:
                        self._writing_ai_reason = ""
                    return loaded
                else:
                    self.ai_coordinator.clear(token=token)
            else:
                self.ai_coordinator.clear(token=token)
        except Exception:
            # Startup probing is optional and runs in a daemon thread. Any
            # normal provider failure must leave the app in rules-only mode.
            logger.warning("Adaptive Polish setup fell back to rules", exc_info=True)
            self._writing_ai_reason = (
                "Local AI could not start. Corrections use built-in rules."
            )
            self.ai_coordinator.clear(token=token)
        finally:
            self.ai_coordinator.finish_update(token)
        return False

    def _warm_adaptive_polish_until_ready(self) -> None:
        """Prewarm at startup, retrying while the GPU is still letting go.

        The startup plan is made once, seconds after launch. Relaunch the app
        while the previous copy's model is still resident - or while any other
        program holds VRAM - and the planner sees no room, picks rules-only,
        and skips the prewarm entirely. Nothing revisits that decision, so the
        user pays a full cold load on their first correction: a 20-character
        sentence took 91 seconds on one such launch, against 2 seconds warm.

        Per-request planning does recover on its own, so this only needs to
        stop the first correction from being the one that waits. A few quiet
        retries cost nothing on a machine that genuinely has no GPU, because
        the loop only runs while an installed, validated pack exists to load.
        """
        for attempt in range(_WARM_RETRY_ATTEMPTS):
            if self._warm_adaptive_polish():
                return
            if not self._gpu_pack_is_installed():
                return
            logger.info(
                "Adaptive Polish prewarm found no room for the model; "
                "retrying in %.0fs (attempt %d of %d)",
                _WARM_RETRY_DELAY_SECONDS,
                attempt + 1,
                _WARM_RETRY_ATTEMPTS,
            )
            if self._warm_retry_stop.wait(_WARM_RETRY_DELAY_SECONDS):
                return

    def _gpu_pack_is_installed(self) -> bool:
        """Whether a model is present to load, so a retry could still succeed."""
        try:
            catalog = self.polish_engine.packs.catalog(refresh_installed=True)
        except Exception:  # noqa: BLE001 - a pack query failure is not fatal here
            return False
        return any(
            bool(getattr(pack, "installed", False))
            and bool(getattr(pack, "validated", False))
            for pack in catalog
        )

    def _refresh_installed_writing_models(self) -> None:
        try:
            manager = LocalWritingModelManager()
            manager.ensure_running(timeout=8.0)
            discovered = manager.list_models(timeout=3.0)
        except (OSError, RuntimeError, ValueError):
            return
        names = {item.name for item in discovered if item.name}
        if names:
            self._installed_writing_models = names

    def _held_pack_id(self) -> str:
        """The pack this session is running, so a busy moment cannot replace it.

        The lease decides, and the stored setting is only the fallback. This is
        the same argument _accepted_writing_model makes below for the model
        name: the lease is the only record of what was really loaded.

        It matters because the two disagree at exactly the wrong moment. The
        prewarm publishes its pack to the coordinator, but nothing writes it
        into engine.settings, so reading the setting returned "" for the first
        request of every session. With no held pack the policy re-decided
        against instantaneous free VRAM, took the smallest pack under load, and
        THAT got accepted - after which holding kept the wrong one.

        Measured 2026-08-26: a tier prewarmed gemma4-12b-qat and then planned
        gemma4-e2b 234 times, with 112 acceptances of e2b. Reading the setting
        made the hold anchor to whatever won the first race; reading the lease
        anchors it to what the session actually started on.
        """
        lease = getattr(self, "ai_coordinator", None)
        lease = lease.lease() if lease is not None else None
        held = str(getattr(getattr(lease, "plan", None), "pack_id", "") or "").strip()
        if held:
            return held
        return str(self.engine.settings.polish_model_pack_id or "").strip()

    def _accepted_writing_model(self) -> str:
        """The model that is actually loaded, not the one settings remember.

        These two drifted apart: settings held gemma4:e2b-it-qat while the
        runtime had prewarmed the e4b pack, and every Writing HUD request named
        the smaller model. It could not finish the repair, so section 3 of
        essay 13 burned four retries and still came back with subject-verb
        agreement unfixed - the section was then discarded, Replace stayed
        disabled, and the review died with eight sections untouched. Passing no
        model at all corrected the same text flawlessly with zero retries.

        The accepted lease is the only record of what was really loaded, so it
        decides. The stored name remains the fallback for the case where
        nothing has been accepted yet.
        """
        lease = self.ai_coordinator.lease()
        accepted = str(
            getattr(getattr(lease, "plan", None), "model", "") or ""
        ).strip()
        if accepted:
            return accepted
        return str(self.engine.settings.ollama_model or "").strip()

    def _dictation_ai_plan_is_current(
        self,
        pack_id: str,
        model: str,
        policy_version: int,
        require_loaded: bool = False,
    ) -> bool:
        return self.ai_coordinator.is_current(
            pack_id,
            model,
            policy_version,
            require_loaded=require_loaded,
        )

    def _share_completed_polish_result(
        self,
        result,
        *,
        published: bool | None = None,
    ) -> None:
        if result is None:
            return
        if published is None:
            token = self.ai_coordinator.begin_update()
            try:
                published = self.ai_coordinator.publish_correction_result(result, token)
            finally:
                self.ai_coordinator.finish_update(token)
        if not published:
            return
        lease = self.ai_coordinator.lease()
        if lease is not None:
            self._installed_writing_models.add(lease.plan.model)

    def _writing_model_for(self, command: str) -> str:
        del command
        return self._accepted_writing_model()

    def _run_writing_command(self) -> None:
        source = self.writing_source.toPlainText().strip()
        if not source:
            update_status_label(self.writing_state, "Highlight text and capture it first.")
            return
        command = self.writing_command_combo.currentText()
        profile = self.writing_profile_combo.currentText()
        model = self._writing_model_for(command)
        instruction = self.writing_instruction_edit.text()
        language = self.writing_language_edit.text()
        protected_terms = tuple(self.store.vocabulary_map().values())
        self._set_writing_actions_enabled(False)
        self.writing_replace_button.setEnabled(False)
        self.writing_copy_button.setEnabled(False)
        update_status_label(self.writing_state, f"Running {command} locally...")

        def work():
            try:
                result = self.writing_assistant.run(
                    source,
                    command=command,
                    model=model,
                    instruction=instruction,
                    language=language,
                    profile=profile,
                    protected_terms=protected_terms,
                    timeout=max(30.0, self.engine.settings.correction_timeout_seconds),
                )
                payload = (True, result.text, f"{command} ready. Review it before replacing.")
            except (RuntimeError, ValueError) as exc:
                payload = (False, "", str(exc))
            self.bridge.writing_result.emit(payload)

        threading.Thread(target=work, daemon=True).start()

    def _handle_writing_result(self, payload) -> None:
        ok, text, message = payload
        self._set_writing_actions_enabled(True)
        update_status_label(self.writing_state, message)
        if not ok:
            return
        self.writing_result_edit.setPlainText(text)
        self.writing_editors.setCurrentWidget(self.writing_result_edit)
        self.writing_replace_button.setEnabled(True)
        self.writing_copy_button.setEnabled(True)

    def _run_palette_command(self, command: str, language: str) -> None:
        if self.selected_text_record is None:
            self.rewrite_palette.show_error("Capture selected text again.")
            return
        source = self.selected_text_record.text.strip()
        model = self._writing_model_for(command)
        profile = self.writing_profile_combo.currentText()
        protected_terms = tuple(self.store.vocabulary_map().values())
        request_id = self._palette_request_id
        logger.info(
            "Writing HUD adaptive Polish request command=%s characters=%d",
            command,
            len(source),
        )

        def work():
            try:
                result = self.writing_assistant.run(
                    source,
                    command=command,
                    model=model,
                    language=language,
                    profile=profile,
                    protected_terms=protected_terms,
                    timeout=max(30.0, self.engine.settings.correction_timeout_seconds),
                )
                payload = (request_id, True, result.text, f"{command} ready. Review before use.")
            except (RuntimeError, ValueError) as exc:
                payload = (request_id, False, "", str(exc))
            self.bridge.palette_writing_result.emit(payload)

        threading.Thread(target=work, daemon=True).start()

    def _run_palette_sentence_rewrite(
        self,
        attempt_id: int,
        suggestion_id: str,
        source: str,
        command: str,
    ) -> None:
        # Automated UI tests must never leak a real local-model request into a
        # later test. Production builds do not set this process-local switch.
        if os.environ.get("ECHOLINK_DISABLE_PALETTE_AI") == "1":
            return
        if self.selected_text_record is None:
            self.rewrite_palette.show_error("Capture selected text again.")
            return
        model = self._writing_model_for(command)
        profile = self.writing_profile_combo.currentText()
        protected_terms = tuple(self.store.vocabulary_map().values())
        request_id = self._palette_request_id
        logger.info(
            "Writing HUD adaptive Polish request command=%s characters=%d",
            command,
            len(source),
        )

        job = (
            request_id,
            int(attempt_id),
            suggestion_id,
            source,
            command,
            model,
            profile,
            protected_terms,
            self.selected_text_record.text or "",
            # Read here, on the GUI thread, because the worker below must not
            # touch a widget. This is the correction THIS SECTION already has,
            # kept so a failed tone does not cost the writer their proofread.
            #
            # It used to read current_replacement_text(), which is whatever is
            # on the card. Those agree for the section the writer is looking at
            # and differ for every section corrected ahead of them in the
            # background - and a background section then had the CARD's text
            # written into it. See correction_for_section for what that did to
            # 122 sections of the acceptance tier.
            self.rewrite_palette.correction_for_section(suggestion_id),
        )
        with self._palette_ai_lock:
            if self._palette_ai_shutdown:
                return
            self._palette_ai_queue.append(job)
            width = self._palette_ai_width()
            spawn = self._palette_ai_workers < min(
                width, len(self._palette_ai_queue) + self._palette_ai_workers
            )
            if spawn:
                self._palette_ai_workers += 1
        if spawn:
            threading.Thread(target=self._palette_ai_worker_loop, daemon=True).start()

    def _palette_ai_width(self) -> int:
        """Use one ordered worker because correction state and GPU residency are shared."""
        return 1

    def _palette_ai_worker_loop(self) -> None:
        while True:
            with self._palette_ai_lock:
                if self._palette_ai_shutdown:
                    self._palette_ai_queue.clear()
                    self._palette_ai_workers -= 1
                    return
                if not self._palette_ai_queue:
                    self._palette_ai_workers -= 1
                    return
                job = self._palette_ai_queue.popleft()
            request_id = job[0]
            if request_id != self._palette_request_id or self._quitting:
                continue
            self._execute_palette_sentence_job(job)

    def _execute_palette_sentence_job(self, job) -> None:
        (
            request_id,
            attempt_id,
            suggestion_id,
            source,
            command,
            model,
            profile,
            protected_terms,
            document_text,
            # Optional so a caller that predates it - including the tests that
            # build this tuple by hand - still works. Absent means "no
            # correction was on the card", which is the safe reading.
            *carried,
        ) = job
        existing_correction = str(carried[0]).strip() if carried else ""
        started_at = time.monotonic()
        runtime_done = threading.Event()
        runtime_state = {
            "model": model,
            "backend": "GPU",
            "reason": "",
        }

        def plan_ready(plan) -> None:
            runtime_state["model"] = plan.model or "AI"
            runtime_state["backend"] = (
                plan.backend.value.upper()
                if plan.backend.value not in {"rules"}
                else "Rules"
            )
            runtime_state["reason"] = plan.downgrade_reason or plan.reason

        threading.Thread(
            target=self._watch_palette_runtime,
            args=(
                request_id,
                attempt_id,
                runtime_state,
                len(source),
                runtime_done,
            ),
            daemon=True,
            name="manuscript-ai-runtime-status",
        ).start()
        hud_timeout = max(
            120.0,
            float(self.engine.settings.correction_timeout_seconds),
        )
        try:
            request_options = {
                "command": command,
                "model": model,
                "profile": profile,
                # PolishEngine and LocalWritingAssistant own the correction
                # contract. Repeating it here caused AI to lose immutable
                # tokens on full-page HUD reviews.
                "instruction": "",
                "protected_terms": protected_terms,
                # The whole capture, not just this section. A word that occurs
                # once in the document is an ordinary word the writer
                # capitalised, not a place; a section on its own cannot tell.
                "document": document_text,
                "timeout": hud_timeout,
                # keep_alive, context_window, max_tokens and require_gpu used to
                # be passed here. They went straight into **_ignored in
                # PolishWritingAssistantAdapter.run and never reached the
                # engine. The hardware policy decides all four on the
                # ExecutionPlan - context_window, max_output_tokens and
                # require_full_gpu - so app-level constants would be fighting it
                # even if they were read. Four settings that look load-bearing
                # and do nothing cost an investigation earlier: they were the
                # first suspect for a correction failure and took a while to
                # rule out.
                "plan_callback": plan_ready,
            }
            result = self.writing_assistant.run(
                source,
                **request_options,
            )
            if result is None or not getattr(result, "text", "").strip():
                # An empty answer is still a finished section. Returning the
                # writer's text keeps the review moving; raising here stranded
                # the paragraph behind a disabled Replace.
                logger.warning(
                    "Writing HUD adaptive Polish returned no text; keeping the "
                    "section unchanged characters=%d",
                    len(source),
                )
                answer = source
            else:
                answer = result.text
            payload = (
                request_id,
                attempt_id,
                suggestion_id,
                True,
                answer,
                (
                    getattr(
                        getattr(self.writing_assistant, "last_correction_result", None),
                        "message",
                        "",
                    )
                    or f"Local {command.lower()} ready."
                ),
            )
            polish_result = getattr(
                self.writing_assistant,
                "last_correction_result",
                None,
            )
            if (
                polish_result is not None
                and not _polish_result_ready_for_review(polish_result)
            ):
                # The AI answer is not offerable - it still disagrees with
                # itself, and test_incomplete_fact_safe_ai_correction_is_not_
                # review_ready is right that it must not be shown. But failing
                # the whole section throws away the deterministic repair too,
                # with every spelling, punctuation and agreement fix already
                # applied. That is what stalled essays 5, 7, 13, 18 and 19 at
                # one section each, leaving the rest of the document
                # uncorrected, and it is inconsistent: when the AI answer is
                # *rejected* the rules repair is shown, so the better outcome
                # was being treated worse than the worse one.
                repaired = str(
                    getattr(polish_result, "deterministic_text", "") or ""
                ).strip()
                tone_request = command.strip().casefold() != "proofread"
                if (
                    tone_request
                    and existing_correction
                    and existing_correction != source.strip()
                ):
                    # A tone is a second pass over a section that already has an
                    # AI proofread on the card. Handing back the rules repair
                    # instead loses the tone AND the correction: measured on
                    # essay 7, a refused Friendly returned "Another benefit are
                    # the WAY a hamster..." with the writer's errors intact.
                    #
                    # UNRESOLVED, 2026-09-12, and left alone rather than guessed
                    # at. For a single-paragraph section this branch cannot be
                    # taken at all: a tone request's `source` IS the section's
                    # current correction, and `existing_correction` is now the
                    # same string, so `existing_correction != source.strip()` is
                    # false and control falls to the deterministic repair below.
                    # Reproduced by driving _execute_palette_sentence_job with a
                    # rejected model answer: the payload came back as the rules
                    # text, which is the essay-7 outcome this branch was written
                    # to prevent.
                    #
                    # It is NOT a regression from correction_for_section. Before
                    # that, `existing_correction` was whatever was on the card,
                    # which for the section the writer is looking at is also
                    # `source` - so the branch was already dead there, and only
                    # came alive for BACKGROUND sections, where the text it
                    # carried belonged to somebody else. It fired exactly when
                    # it had the wrong answer.
                    #
                    # The fix is probably to drop the `!=` test and keep the
                    # correction whenever a tone request carries one. That
                    # changes which text a writer gets when a tone fails, so it
                    # is a correction-quality change and this project measures
                    # those through Word, never by reasoning. Whoever picks this
                    # up: reconstruct the essay-7 case first, because the
                    # comment above is the only record of what `source` held
                    # when it was measured.
                    #
                    # Reporting the section as failed was tried first and was
                    # worse still - it disables Replace, so seven sections were
                    # dismissed uncorrected and the essay went from 0 unknown
                    # words to 18. The correction has to come back, applied,
                    # with the truth in the message.
                    logger.info(
                        "Writing HUD kept the existing correction; the %s "
                        "rewrite could not be applied characters=%d",
                        command,
                        len(existing_correction),
                    )
                    payload = (
                        request_id,
                        attempt_id,
                        suggestion_id,
                        True,
                        existing_correction,
                        f"{command} could not be applied; the correction "
                        "already found is unchanged.",
                    )
                elif repaired and repaired != source.strip():
                    logger.info(
                        "Writing HUD offering the deterministic repair for a "
                        "section the AI could not finish characters=%d",
                        len(repaired),
                    )
                    payload = (
                        request_id,
                        attempt_id,
                        suggestion_id,
                        True,
                        repaired,
                        "Rules correction ready; the AI could not finish this "
                        "section.",
                    )
                else:
                    # Nothing safe to change, which is not the same as a
                    # failure. Refusing the section here disabled Replace and
                    # reported "could not be corrected", leaving the reader a
                    # dead card and no way to move that paragraph forward. A
                    # section must always come back; returning the writer's own
                    # text says "nothing to change" honestly, and the rejected
                    # AI answer is still never shown.
                    logger.info(
                        "Writing HUD returning the section unchanged; no safe "
                        "correction was available characters=%d",
                        len(source),
                    )
                    payload = (
                        request_id,
                        attempt_id,
                        suggestion_id,
                        True,
                        source,
                        "No changes to suggest for this section.",
                    )
            logger.info(
                "Writing HUD adaptive Polish completed command=%s characters=%d backend=%s",
                command,
                len(answer),
                "adaptive",
            )
            if polish_result is not None and not polish_result.used_llm:
                # A section the AI was refused on still shows a review card and
                # still says Accept, so from outside it is indistinguishable
                # from a full correction. Name it, with the guard that refused
                # it: a run where nine of twenty paragraphs were rules-only was
                # reported as twenty successes, and nothing in the log said
                # otherwise.
                # refusal_reason carries the reader-facing sentence, which
                # says a rejection happened but not what refused it. On
                # 2026-08-18 essay 16 lost two of ten sections this way and
                # the log said only "AI output was rejected", so the guard had
                # to be found by reading the engine. The validation failures
                # are the actual answer and they are free to print.
                failures = tuple(
                    getattr(polish_result.validation, "failures", ()) or ()
                )
                # residual_errors as well as validation.failures, because the
                # failures list came back empty on the path that matters and
                # left "guards=unrecorded" as the only clue. Between the two,
                # one of them names what refused the section.
                residual = tuple(getattr(polish_result, "residual_errors", ()) or ())
                logger.warning(
                    "Writing HUD section fell back to rules command=%s "
                    "characters=%d reason=%s guards=%s residual=%s rejected_llm=%s",
                    command,
                    len(source),
                    str(polish_result.metrics.get("refusal_reason") or "unrecorded"),
                    "; ".join(str(value) for value in failures) or "unrecorded",
                    "; ".join(str(value) for value in residual) or "none",
                    getattr(polish_result, "rejected_llm", "unknown"),
                )
            duration = max(0.1, time.monotonic() - started_at)
            if polish_result is None or polish_result.used_llm:
                sample = {
                    "model": (
                        polish_result.plan.model
                        if polish_result is not None
                        else model
                    ),
                    "backend": (
                        polish_result.plan.backend.value.upper()
                        if polish_result is not None
                        else "GPU"
                    ),
                    "seconds": round(duration, 3),
                    "characters": max(1, len(source)),
                }
                self._palette_eta_sample = sample
                try:
                    self.store.set_meta("writing_runtime_eta", sample)
                except (OSError, RuntimeError, TypeError, ValueError):
                    logger.warning("Writing HUD could not save runtime ETA sample")
        except Exception as exc:
            # This is a daemon UI worker boundary. Any normal runtime/provider
            # failure must become a safe HUD error instead of escaping the thread.
            failed_result = getattr(
                self.writing_assistant, "last_correction_result", None
            )
            logger.warning(
                "Writing HUD adaptive Polish failed command=%s: %s | "
                "validation=%s residual=%s used_llm=%s retries=%s",
                command,
                exc,
                list(getattr(getattr(failed_result, "validation", None), "failures", ()) or ()),
                list(getattr(failed_result, "residual_errors", ()) or ()),
                getattr(failed_result, "used_llm", None),
                getattr(failed_result, "retries", None),
            )
            message = str(exc)
            if (
                    "too long" in message.casefold()
                    or time.monotonic() - started_at >= hud_timeout - 0.25
            ):
                message = "AI took too long. Showing the instant local correction."
            elif "too long for one local ai request" in message.casefold():
                message = "Text is too long for one AI check. Showing the instant local correction."
            # A section must always come back with something the reader can
            # act on. The rules repair if the engine produced one, otherwise
            # the writer's own text. Only a failure with neither - no capture
            # to fall back to - still reports an error.
            repaired = str(getattr(failed_result, "deterministic_text", "") or "").strip()
            recovered = repaired or source.strip()
            if recovered:
                payload = (
                    request_id,
                    attempt_id,
                    suggestion_id,
                    True,
                    recovered,
                    message,
                )
            else:
                payload = (request_id, attempt_id, suggestion_id, False, "", message)
        finally:
            runtime_done.set()
        with self._palette_ai_lock:
            should_emit = not self._palette_ai_shutdown
        if should_emit:
            self.bridge.palette_sentence_result.emit(payload)

    def _watch_palette_runtime(
        self,
        request_id: int,
        attempt_id: int,
        runtime_state: dict[str, str],
        source_characters: int,
        done: threading.Event,
    ) -> None:
        def shutting_down() -> bool:
            with self._palette_ai_lock:
                return self._palette_ai_shutdown

        if shutting_down():
            return
        model = runtime_state.get("model") or "AI"
        backend_hint = runtime_state.get("backend") or "GPU"
        estimate = self._estimated_palette_eta(model, source_characters, backend_hint)
        try:
            self.bridge.palette_sentence_progress.emit(
                (request_id, attempt_id, "Checking local resources", estimate)
            )
        except RuntimeError:
            return
        if done.wait(0.5):
            return
        while not done.is_set():
            if shutting_down():
                return
            model = runtime_state.get("model") or model
            backend_hint = runtime_state.get("backend") or backend_hint
            label = f"Loading AI on {backend_hint}"
            backend = backend_hint
            if backend_hint == "Rules":
                label = runtime_state.get("reason") or "Using instant local checks"
            try:
                status = self.writing_assistant.runtime_status(model, timeout=0.75)
                backend = str(getattr(status, "backend", backend_hint))
                if status.loaded and backend == "GPU":
                    gib = status.gpu_bytes / (1024**3)
                    label = f"GPU ({gib:.1f} GB VRAM)"
                elif status.loaded and backend == "Hybrid":
                    label = "GPU/CPU split detected; GPU-only mode stopping"
                elif status.loaded:
                    label = f"AI ready on {backend}"
                else:
                    label = f"Loading AI on {backend_hint}"
            except Exception:
                label = f"Checking {backend_hint} runtime"
            estimate = self._estimated_palette_eta(
                model,
                source_characters,
                backend,
            )
            try:
                self.bridge.palette_sentence_progress.emit(
                    (request_id, attempt_id, label, estimate)
                )
            except RuntimeError:
                return
            if done.wait(1.0):
                return

    def _estimated_palette_eta(
        self,
        model: str,
        source_characters: int,
        backend: str,
    ) -> float | None:
        sample = self._palette_eta_sample
        if (
            str(sample.get("model") or "").casefold() != str(model).casefold()
            or str(sample.get("backend") or "").casefold() != str(backend).casefold()
        ):
            return None
        try:
            seconds = float(sample["seconds"])
            characters = max(1, int(sample["characters"]))
        except (KeyError, TypeError, ValueError):
            return None
        if not math.isfinite(seconds) or seconds <= 0:
            return None
        scale = max(0.5, min(3.0, max(1, source_characters) / characters))
        return max(1.0, seconds * scale)

    def _handle_palette_writing_result(self, payload) -> None:
        request_id, ok, text, message = payload
        if request_id != self._palette_request_id or self.selected_text_record is None:
            return
        message = user_ai_status(message)
        update_status_label(self.writing_state, message, assertive=not ok)
        if not ok:
            self.rewrite_palette.show_error(message)
            return
        self.writing_result_edit.setPlainText(text)
        self.writing_editors.setCurrentWidget(self.writing_result_edit)
        self.writing_replace_button.setEnabled(True)
        self.writing_copy_button.setEnabled(True)
        self.rewrite_palette.show_result(text, message)

    def _handle_palette_sentence_result(self, payload) -> None:
        request_id, attempt_id, suggestion_id, ok, text, message = payload
        if request_id != self._palette_request_id or self.selected_text_record is None:
            return
        message = user_ai_status(message)
        update_status_label(self.writing_state, message, assertive=not ok)
        if not ok:
            self.rewrite_palette.show_sentence_error(attempt_id, suggestion_id, message)
            return
        self.rewrite_palette.show_sentence_result(
            attempt_id,
            suggestion_id,
            text,
            message,
        )

    def _handle_palette_sentence_progress(self, payload) -> None:
        request_id, attempt_id, backend, eta_seconds = payload
        if request_id != self._palette_request_id or self.selected_text_record is None:
            return
        self.rewrite_palette.show_sentence_progress(
            attempt_id,
            backend,
            eta_seconds,
        )

    def _show_palette_target(self, start: int, end: int) -> None:
        # Every failure here is silent, and the writer sees only that the red
        # underline stopped appearing. Curtis reported exactly that on
        # 2026-09-01 with nothing in the log to say which of the three ways it
        # gives up had happened: no capture to measure against, a reader
        # without the rectangle call, or a lookup that returned no rectangles.
        # Offsets and a count only, never the text.
        record = self.selected_text_record
        if record is None or start < 0 or end <= start:
            logger.info(
                "Writing HUD target underline cleared: record=%s start=%d end=%d",
                record is not None,
                start,
                end,
            )
            self.target_underline.clear()
            return
        reader = WindowsSelectionReader(max_characters=500_000)
        rectangle_reader = getattr(reader, "text_rectangles", None)
        if not callable(rectangle_reader):
            logger.warning(
                "Writing HUD target underline unavailable: the selection reader "
                "has no text_rectangles"
            )
            self.target_underline.clear()
            return
        rectangles = rectangle_reader(record, start, end, scroll_into_view=True)
        logger.info(
            "Writing HUD target underline start=%d end=%d rectangles=%d "
            "record_chars=%d scope=%s",
            start,
            end,
            len(rectangles or ()),
            len(str(record.text or "")),
            record.scope,
        )
        self.target_underline.show_rectangles(rectangles)

    def _check_palette_focus(self) -> None:
        # Released here, on a Qt timer, and not only from the selection
        # monitor's cleared callback.
        #
        # The owner reported the HUD staying in front of everything without
        # ever pressing Pin. The window is created with WindowStaysOnTopHint
        # and set_stays_on_top(True) is called the moment the writer is
        # detected in a document, so the only thing that can lower it again is
        # _release_hud_top_if_focus_left. That had exactly one caller,
        # _handle_automatic_selection_cleared, which runs from the selection
        # monitor - and the monitor returns immediately while it is paused.
        # It is paused for the whole time a review is being worked on. So
        # during a review the HUD could not be lowered by anything, which is
        # indistinguishable from pinned.
        #
        # This timer already runs every 175ms whatever the monitor is doing,
        # and the release is guarded and cheap: it returns at once when the
        # HUD is hidden or genuinely pinned.
        self._release_hud_top_if_focus_left()
        record = self.selected_text_record
        if (
            record is None
            or not self.rewrite_palette.isVisible()
            or self.rewrite_palette.view == "badge"
        ):
            return
        current = active_window()
        if current.process_id == os.getpid():
            return
        if current.handle == record.window.handle:
            reader = WindowsSelectionReader(max_characters=500_000)
            if reader.is_record_control_focused(record):
                self.rewrite_palette.set_target_marking_enabled(True)
                return
        if self.rewrite_palette.user_opened:
            self.rewrite_palette.set_target_marking_enabled(False)
            self.target_underline.clear()
            return
        logger.info("Writing HUD closed because focus left the captured text box")
        self.rewrite_palette.reject()

    def _replace_palette_result(self, text: str) -> None:
        if self.selected_text_record is None or not text.strip():
            return
        scope = self.selected_text_record.scope
        try:
            record = self.engine.inserter.replace_selection(self.selected_text_record, text)
        except (RuntimeError, ValueError) as exc:
            logger.warning("Writing HUD apply failed: %s", exc)
            self._clear_palette_review_state()
            self.rewrite_palette.require_recapture(str(exc))
            self._resume_selection_monitor()
            return
        if record is None:
            scope_name = "document" if scope == "document" else "selection"
            message = f"The {scope_name} changed or could not be rechecked. Nothing was replaced. Capture it again."
            logger.warning("Writing HUD apply skipped because the %s could not be rechecked", scope_name)
            update_status_label(self.writing_state, message, assertive=True)
            self._clear_palette_review_state()
            self.rewrite_palette.require_recapture(message)
            self._resume_selection_monitor()
            return
        self.rewrite_palette.finish_choice()
        self._clear_palette_review_state()
        self._automatic_hud_suppress_until = time.monotonic() + 1.5
        self._resume_selection_monitor()
        update_status_label(
            self.writing_state,
            "Document updated." if scope == "document" else "Selection updated.",
        )

    def _undo_palette_step(self, previous_text: str, suggestion: object) -> bool:
        """Reverse the correction just written, by writing the original back.

        Deliberately not `inserter.undo_last()`, which sends Ctrl+Z. That is
        the editor's undo stack, not ours: it is gated on the same window and
        120 seconds, and in Word it takes back whatever the writer did last,
        which after a correction is often their own typing. Replaying the
        reverse edit through the same range path touches exactly the span this
        correction touched and nothing else.
        """
        current = self.selected_text_record
        if current is None or not previous_text.strip():
            return False
        replacement = str(getattr(suggestion, "replacement", ""))
        original = str(getattr(suggestion, "original", ""))
        try:
            if record_uses_word_document_adapter(current):
                start = int(getattr(suggestion, "start"))
                # The span moved when the replacement went in: it now runs from
                # the same start for the length of what was written, not of
                # what was there before.
                restored = self.engine.inserter.replace_document_range(
                    current,
                    expected_document_text=current.text,
                    start=start,
                    end=start + len(replacement),
                    expected_text=replacement,
                    new_text=original,
                )
            else:
                restored = self.engine.inserter.replace_selection(
                    current, previous_text
                )
        except (RuntimeError, ValueError) as exc:
            logger.warning("Writing HUD undo failed: %s", exc)
            return False
        if restored is None:
            logger.warning(
                "Writing HUD undo could not recheck the text it applied"
            )
            return False
        if isinstance(restored, SelectionRecord):
            self.selected_text_record = restored
        self.writing_source.setPlainText(previous_text)
        self._automatic_hud_suppress_until = time.monotonic() + 1.5
        logger.info("Writing HUD undo restored characters=%d", len(original))
        update_status_label(self.writing_state, "Correction undone.")
        return True

    def _replace_palette_step(self, text: str, suggestion: object) -> bool:
        """Apply one accepted HUD correction now and preserve the remaining review."""
        current = self.selected_text_record
        if current is None or not text.strip():
            return False
        try:
            if record_uses_word_document_adapter(current):
                inserted = self.engine.inserter.replace_document_range(
                    current,
                    expected_document_text=current.text,
                    start=int(getattr(suggestion, "start")),
                    end=int(getattr(suggestion, "end")),
                    expected_text=str(getattr(suggestion, "original")),
                    new_text=str(getattr(suggestion, "replacement")),
                )
            else:
                inserted = self.engine.inserter.replace_selection(current, text)
        except (RuntimeError, ValueError) as exc:
            logger.warning("Writing HUD immediate apply failed: %s", exc)
            self.rewrite_palette.show_error(
                f"This correction was not applied. {exc}"
            )
            return False
        if inserted is None:
            # A first failure is usually a race - a repaint, or focus moving
            # for an instant - and retrying works, so the review is kept.
            # A second failure on the same correction means the source itself
            # has moved, and then the captured text can never match again:
            # "try Replace Text again" asks for the one thing that cannot
            # work. That advice was followed a dozen times in a row against a
            # document Word had quietly swapped for an AutoRecovered copy.
            self._failed_apply_streak += 1
            if self._failed_apply_streak < _APPLY_RETRY_BEFORE_RECAPTURE:
                message = (
                    "This correction was not applied. The document may have "
                    "been busy. Try Replace Text again."
                )
                logger.warning(
                    "Writing HUD immediate apply could not recheck the source "
                    "text; retry %d of %d",
                    self._failed_apply_streak,
                    _APPLY_RETRY_BEFORE_RECAPTURE,
                )
                self.rewrite_palette.show_error(message)
                return False
            message = (
                "The document changed since it was captured, so nothing was "
                "replaced. Capture it again to review the current text."
            )
            logger.warning(
                "Writing HUD immediate apply failed %d times; asking for a "
                "fresh capture",
                self._failed_apply_streak,
            )
            update_status_label(self.writing_state, message, assertive=True)
            self._clear_palette_review_state()
            self.rewrite_palette.require_recapture(message)
            # Word still holds the selection that was being reviewed - often
            # the whole document. Resuming the monitor immediately re-captured
            # that same selection as one enormous section, which is why the
            # next card showed the entire essay and the counter read "0 of 0".
            # Give the writer time to place a new selection first.
            self._automatic_hud_suppress_until = (
                time.monotonic() + _RECAPTURE_SUPPRESS_SECONDS
            )
            self._resume_selection_monitor()
            return False
        self._failed_apply_streak = 0

        if isinstance(inserted, SelectionRecord):
            self.selected_text_record = inserted
            self.writing_source.setPlainText(text)
            self._automatic_hud_suppress_until = time.monotonic() + 1.5
            logger.info(
                "Writing HUD accepted stable Word range characters=%d",
                len(text),
            )
            update_status_label(
                self.writing_state,
                "Correction applied. Next section ready.",
            )
            return True

        if record_covers_entire_document(current):
            # Re-read the document so the next section's record carries the
            # editor's own line-ending convention. Synthesizing the record
            # from the pasted text left "\n" where Notepad reports "\r",
            # which made every later recheck fail as "document changed".
            # Word keeps its stable COM-range record instead: its replace
            # path never requires a second focus-dependent read. The read
            # retries once because it can race the post-paste repaint.
            reread = None
            if not record_uses_word_document_adapter(current):
                reread_reader = WindowsSelectionReader(max_characters=500_000)
                reread = reread_reader.read_document()
                if reread is None:
                    time.sleep(0.2)
                    reread = reread_reader.read_document()
            if (
                reread is not None
                and reread.record.window.handle == current.window.handle
            ):
                self.selected_text_record = reread.record
                text = reread.record.text
            else:
                self.selected_text_record = SelectionRecord(
                    text,
                    current.window,
                    "document",
                    hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    current.control_runtime_id,
                    current.control_anchor,
                )
            self.writing_source.setPlainText(text)
            self._automatic_hud_suppress_until = time.monotonic() + 1.5
            logger.info(
                "Writing HUD accepted document correction applied immediately characters=%d",
                len(text),
            )
            update_status_label(self.writing_state, "Correction applied. Next section ready.")
            return True

        reader = WindowsSelectionReader(max_characters=500_000)
        # The read races the editor repainting after the paste: an instant
        # read can land in a brief UI Automation blackout and return None
        # even though the document is fine. Retry before giving up.
        detected = None
        for attempt in range(3):
            detected = reader.read_document()
            if (
                detected is not None
                and detected.record.window.handle == current.window.handle
            ):
                break
            self.engine.inserter.activate_window(current.window)
            time.sleep(0.15 + 0.2 * attempt)
        if detected is None or detected.record.window.handle != current.window.handle:
            message = "The correction was inserted, but Manuscript could not prepare the next section. Select the text again to continue."
            logger.warning("Writing HUD inserted a step but could not read the updated document")
            self.rewrite_palette.require_recapture(message)
            self._clear_palette_review_state()
            self._resume_selection_monitor()
            return False

        document = detected.record
        if current.scope == "document" or _same_document_text(document.text, text):
            next_record = document
        else:
            matched = _unique_document_text(document.text, text)
            if matched is None:
                message = "The correction was inserted, but Manuscript could not uniquely locate it for the next section. Select the text again to continue."
                logger.warning("Writing HUD inserted a step but could not uniquely relocate it")
                self.rewrite_palette.require_recapture(message)
                self._clear_palette_review_state()
                self._resume_selection_monitor()
                return False
            next_record = SelectionRecord(
                matched,
                current.window,
                "selection",
                document.document_digest,
            )
            if not reader.restore_selection(next_record):
                message = "The correction was inserted, but Manuscript could not keep that text selected for the next section. Select it again to continue."
                logger.warning("Writing HUD inserted a step but could not restore the updated selection")
                self.rewrite_palette.require_recapture(message)
                self._clear_palette_review_state()
                self._resume_selection_monitor()
                return False
        self.selected_text_record = next_record
        self.writing_source.setPlainText(text)
        self._automatic_hud_suppress_until = time.monotonic() + 1.5
        logger.info(
            "Writing HUD accepted correction applied immediately characters=%d",
            len(text),
        )
        update_status_label(self.writing_state, "Correction applied. Next section ready.")
        return True

    def _remember_reviewed_text(self) -> None:
        """Record the document as it stands now that its review has finished.

        Recorded after the corrections went in, not before, because the next
        automatic capture reads the corrected text. Essay 1 on 2026-08-15
        finished at 9568 characters and re-offered itself at 9555: the same
        document, and the only text worth remembering is the second one.
        """
        record = self.selected_text_record
        if record is None:
            return
        text = _normalized_document_text(record.text)
        if not text:
            return
        if text not in self._reviewed_texts:
            self._reviewed_texts.append(text)
        # A repeated review still completed. Suppress duplicate cache entries,
        # never the completion acknowledgement used by the user-test harness.
        logger.info(
            "Writing HUD review finished; will not auto-offer this text again "
            "chars=%d remembered=%d",
            len(text),
            len(self._reviewed_texts),
        )

    def _finish_palette_applied(self) -> None:
        self._remember_reviewed_text()
        self._clear_palette_review_state()
        self._automatic_hud_suppress_until = time.monotonic() + 1.5
        self._resume_selection_monitor()
        update_status_label(self.writing_state, "All accepted corrections were applied.")

    def _stage_palette_result(self, text: str) -> None:
        if not text.strip():
            return
        self._stage_private_text(text)
        self.rewrite_palette.finish_choice()
        self._clear_palette_review_state()
        self._resume_selection_monitor()
        update_status_label(
            self.writing_state,
            "Result staged for Manuscript private paste. Windows clipboard unchanged.",
        )

    def _reject_palette_result(self) -> None:
        self._clear_palette_review_state()
        self._resume_selection_monitor()
        update_status_label(
            self.writing_state,
            "Result rejected. Original text was not changed.",
        )

    def _finish_palette_unchanged(self) -> None:
        self._remember_reviewed_text()
        self._clear_palette_review_state()
        self._resume_selection_monitor()
        update_status_label(
            self.writing_state,
            "No changes needed. Original text kept.",
        )

    def _clear_palette_review_state(self) -> None:
        self.target_underline.clear()
        self._palette_request_id += 1
        # A fresh capture starts its own retry budget.
        self._failed_apply_streak = 0
        self.selected_text_record = None
        self.writing_result_edit.clear()
        self.writing_replace_button.setEnabled(False)
        self.writing_copy_button.setEnabled(False)

    def _choose_writing_command(self, command: str) -> None:
        index = self.writing_command_combo.findText(command)
        if index >= 0:
            self.writing_command_combo.setCurrentIndex(index)
        self._run_writing_command()

    def _set_writing_actions_enabled(self, enabled: bool) -> None:
        self.writing_run_button.setEnabled(enabled)
        for button in self.writing_quick_buttons.values():
            button.setEnabled(enabled)

    def _replace_selected_text(self) -> None:
        text = self.writing_result_edit.toPlainText().strip()
        if self.selected_text_record is None or not text:
            return
        record = self.engine.inserter.replace_selection(self.selected_text_record, text)
        if record is None:
            update_status_label(
                self.writing_state,
                "Selection changed. Nothing was replaced. Capture it again.",
                assertive=True,
            )
            return
        update_status_label(self.writing_state, "Selection replaced.")
        self.writing_replace_button.setEnabled(False)

    def _copy_writing_result(self) -> None:
        text = self.writing_result_edit.toPlainText().strip()
        if text:
            self._stage_private_text(text)
            update_status_label(
                self.writing_state,
                "Result staged for Manuscript private paste.",
            )

    def _reject_writing_result(self) -> None:
        self.writing_result_edit.clear()
        self.writing_replace_button.setEnabled(False)
        self.writing_copy_button.setEnabled(False)
        update_status_label(
            self.writing_state,
            "Result rejected. Original text was not changed.",
        )

    def _add_local_sources(self) -> None:
        paths, _filter = QFileDialog.getOpenFileNames(
            self,
            "Approve local text sources",
            "",
            "Text sources (*.txt *.md)",
        )
        if not paths:
            return
        results = self.local_source_index.approve(paths)
        rejected = [item for item in results if not item.indexed]
        self._refresh_local_source_state()
        if rejected:
            self.source_results.setPlainText(
                "Some files were not added:\n"
                + "\n".join(f"- {item.path}: {item.reason}" for item in rejected)
            )

    def _clear_local_sources(self) -> None:
        self._source_chat_request_id += 1
        self.local_source_index.clear()
        self.source_query.clear()
        self.source_results.clear()
        self.source_chat_question.clear()
        self.source_chat_answer.clear()
        self.source_add_button.setEnabled(True)
        self.source_clear_button.setEnabled(True)
        self.source_chat_button.setEnabled(True)
        update_status_label(
            self.source_chat_state,
            "No chat answer. Session only.",
        )
        self._refresh_local_source_state()

    def _refresh_local_source_state(self) -> None:
        count = len(self.local_source_index.approved_paths)
        update_status_label(
            self.source_state,
            f"{count} local source file(s) approved for this session."
            if count
            else "No sources approved.",
        )

    def _use_captured_text_for_sources(self) -> None:
        text = self.writing_source.toPlainText().strip() or self.latest_text.strip()
        if not text:
            update_status_label(self.source_state, "Capture selected text or dictate text first.")
            return
        self.source_query.setPlainText(text)

    def _search_local_sources(self) -> None:
        query = self.source_query.toPlainText().strip()
        if not query:
            update_status_label(self.source_state, "Enter text to search.")
            return
        if not self.local_source_index.approved_paths:
            update_status_label(self.source_state, "Approve at least one local source first.")
            return
        matches = self.local_source_index.search(query, limit=20)
        if not matches:
            self.source_results.setPlainText("No matching local source lines found.")
            return
        lines = ["Local source matches:", ""]
        for number, match in enumerate(matches, start=1):
            lines.extend(
                (
                    f"[{number}] {match.citation}",
                    match.snippet,
                    "",
                )
            )
        self.source_results.setPlainText("\n".join(lines).rstrip())

    def _check_local_similarity(self) -> None:
        text = self.source_query.toPlainText().strip()
        paths = self.local_source_index.approved_paths
        if not text:
            update_status_label(self.source_state, "Enter text to check.")
            return
        if not paths:
            update_status_label(self.source_state, "Approve at least one local source first.")
            return
        self.source_similarity_button.setEnabled(False)
        self.source_results.setPlainText("Checking approved local files...")

        def work() -> None:
            try:
                report = compare_local_sources(text, paths)
                message = (
                    report.as_text()
                    + "\n\nLocal comparison only. This is not an online plagiarism verdict."
                )
            except (OSError, ValueError) as exc:
                message = f"Local similarity check failed: {exc}"
            self.bridge.source_report.emit(message)

        threading.Thread(target=work, daemon=True, name="echo-link-local-similarity").start()

    def _handle_source_report(self, text: str) -> None:
        self.source_results.setPlainText(text)
        self.source_similarity_button.setEnabled(True)

    def _ask_local_sources(self) -> None:
        question = self.source_chat_question.toPlainText().strip()
        if not question:
            update_status_label(self.source_chat_state, "Enter a question first.")
            return
        if not self.local_source_index.approved_paths:
            update_status_label(
                self.source_chat_state,
                "Approve at least one local source first.",
            )
            return
        model = self._accepted_writing_model()
        if not model:
            update_status_label(
                self.source_chat_state,
                "AI is still getting ready. Try again shortly.",
            )
            return
        self._source_chat_request_id += 1
        request_id = self._source_chat_request_id
        self.source_add_button.setEnabled(False)
        self.source_clear_button.setEnabled(False)
        self.source_chat_button.setEnabled(False)
        self.source_chat_answer.clear()
        update_status_label(
            self.source_chat_state,
            "Asking AI on this computer...",
        )

        def work() -> None:
            try:
                if not self.engine.prepare_ai_for_execution(
                    timeout=max(30.0, self.engine.settings.correction_timeout_seconds)
                ):
                    raise RuntimeError("Local AI is not ready. Try again shortly.")
                result = self.source_chat.ask(
                    question,
                    sources=self.local_source_index,
                    model=model,
                    timeout=max(30.0, self.engine.settings.correction_timeout_seconds),
                )
                payload = (
                    request_id,
                    True,
                    result,
                    f"Answer ready from {len(result.citations)} approved excerpt(s). Session only.",
                )
            except (RuntimeError, TypeError, ValueError) as exc:
                payload = (request_id, False, None, str(exc))
            self.bridge.source_chat_result.emit(payload)

        threading.Thread(
            target=work,
            daemon=True,
            name="echo-link-approved-source-chat",
        ).start()

    def _handle_source_chat_result(self, payload) -> None:
        request_id, ok, result, message = payload
        if request_id != self._source_chat_request_id:
            return
        self.source_add_button.setEnabled(True)
        self.source_clear_button.setEnabled(True)
        self.source_chat_button.setEnabled(True)
        message = user_ai_status(message)
        update_status_label(self.source_chat_state, message, assertive=not ok)
        if not ok or not isinstance(result, LocalChatResult):
            return
        lines = [result.answer, "", "Approved excerpts used (session only):", ""]
        for citation in result.citations:
            lines.extend(
                (
                    f"[{citation.source_id}] {citation.location}",
                    citation.excerpt,
                    "",
                )
            )
        self.source_chat_answer.setPlainText("\n".join(lines).rstrip())

    def _show_ready_state(self) -> None:
        hotkey = self.engine.settings.hotkey.upper().replace("+", " + ")
        writing_hud = self.engine.settings.command_hotkey.upper().replace("+", " + ")
        private_paste = self.engine.settings.private_paste_hotkey.upper().replace("+", " + ")
        label = (
            f"Dictate {hotkey} | Writing HUD {writing_hud} | Private paste {private_paste}"
        )
        self.hotkey_label.setText(label)
        if self.enable_hotkey and (self.hotkey is None or not self.hotkey.registered):
            message = self.hotkey.registration_error if self.hotkey is not None else ""
            self._set_status(
                message or "The global hotkey did not start. Open Settings and try another shortcut.",
                COLORS["amber"],
            )
            return
        if self.enable_hotkey and self.engine.settings.hands_free_enabled and (
            self.hands_free_hotkey is None or not self.hands_free_hotkey.registered
        ):
            message = self.hands_free_hotkey.registration_error if self.hands_free_hotkey else ""
            self._set_status(message or "The hands-free hotkey did not start.", COLORS["amber"])
            return
        if self.enable_hotkey and (self.command_hotkey is None or not self.command_hotkey.registered):
            message = self.command_hotkey.registration_error if self.command_hotkey else ""
            self._set_status(message or "The selected-text hotkey did not start.", COLORS["amber"])
            return
        if self.enable_hotkey and (
            self.private_paste_hotkey is None or not self.private_paste_hotkey.registered
        ):
            message = (
                self.private_paste_hotkey.registration_error
                if self.private_paste_hotkey is not None
                else ""
            )
            self._set_status(message or "The private paste hotkey did not start.", COLORS["amber"])
            return
        speech_status = self.engine.transcriber.status()
        if not speech_status["available"]:
            self._set_status("Speech AI unavailable. Open Settings to repair it.", COLORS["amber"])
        elif self._writing_ai_reason:
            # SAY IT HERE, because nothing else does.
            #
            # Coming up rules-only turns the product's main feature off, and it
            # was visible only as a WARNING in a log the writer has no reason to
            # open. This panel already reports a hotkey that would not register
            # and a speech model that would not load; the corrector going
            # missing is at least as worth saying.
            #
            # Amber rather than red: the cause is usually momentary - another
            # program holding the GPU at launch - and every later request
            # re-plans, so this describes the warm-up rather than a permanent
            # state.
            self._set_status(self._writing_ai_reason, COLORS["amber"])
        else:
            self._set_status("Ready", COLORS["green"])

    def _set_status(self, text: str, color: str) -> None:
        update_status_label(
            self.status_text,
            text,
            assertive=color == COLORS["red"],
        )
        self.state_dot.setStyleSheet(
            f"background: {color}; border-radius: 6px; min-width: 12px; max-width: 12px; "
            "min-height: 12px; max-height: 12px;"
        )

    def _set_mode(self, mode: DictationMode, *, save: bool = True) -> None:
        self.mode_buttons[mode].setChecked(True)
        if save and self.engine.settings.mode is not mode:
            settings = self.store.get_settings()
            settings.mode = mode
            self.engine.update_settings(settings)

    def _set_review_mode(self, enabled: bool) -> None:
        if self.engine.settings.review_before_insert == bool(enabled):
            return
        settings = self.store.get_settings()
        settings.review_before_insert = bool(enabled)
        self.engine.update_settings(settings)

    def _toggle_manual_recording(self) -> None:
        self.engine.toggle_recording(insert_result=False)

    def _capture_dictation_hotkey_target(self) -> None:
        """Remember the editor before queued UI work can change foreground focus."""
        self.bridge.hotkey_pressed.emit(active_window())

    def _hotkey_press(self, target_window=None) -> None:
        # The captured target identifies where this dictation belongs. Do not
        # reactivate it here: moving foreground focus can move the caret and
        # the engine already allows capture while earlier AI jobs process.
        if self.engine.can_start_recording:
            self.engine.start_recording(target_window=target_window)

    def _hotkey_release(self) -> None:
        if self.engine.state is SessionState.LISTENING and not self.engine.hands_free_session:
            self.engine.stop_recording()

    def _toggle_hands_free_recording(self) -> None:
        if self.engine.hands_free_session:
            self.engine.stop_recording()
        elif self.engine.can_start_recording:
            self.engine.start_recording(hands_free=True)

    def _cancel_hands_free_recording(self) -> None:
        if self.engine.hands_free_session:
            self.engine.cancel_recording()

    def _handle_session_event(self, event: SessionEvent) -> None:
        previous_state = self._last_session_state
        self._last_session_state = event.state
        explicit_capture_event = bool(event.capture_started or event.capture_stopped)
        capture_started = bool(event.capture_started)
        capture_stopped = bool(event.capture_stopped)
        # Keep older tests and third-party event adapters working. Real engine
        # events carry capture/job IDs, so interleaved processing updates can
        # never masquerade as microphone transitions.
        if not explicit_capture_event and not event.capture_id and not event.job_sequence:
            capture_started = bool(
                event.state is SessionState.LISTENING
                and previous_state is not SessionState.LISTENING
            )
            capture_stopped = bool(
                previous_state is SessionState.LISTENING
                and event.state is not SessionState.LISTENING
            )
        capture_is_active = bool(
            self.engine.state is SessionState.LISTENING
            or (capture_started and not capture_stopped)
        )
        if (
            self.engine.settings.pause_media_during_dictation
            and capture_started
        ):
            self.media_pause.pause()
        elif capture_stopped:
            self.media_pause.resume()
        if self.tray_icon is not None:
            self.tray_icon.setToolTip(f"Manuscript - {event.message}")
            self.tray_dictate_action.setText(
                "Stop dictation" if capture_is_active else "Dictate in Manuscript"
            )
            self.tray_hands_free_action.setText(
                "Stop hands-free dictation" if self.engine.hands_free_session else "Start hands-free dictation"
            )
        if self.engine.settings.show_echo_bar:
            self.echo_bar.show_state(event.state, event.message)
        if self.engine.settings.sound_enabled:
            if capture_started:
                play_start(self.engine.settings)
            elif capture_stopped:
                play_stop(self.engine.settings)
        color = COLORS["green"]
        if capture_is_active or (capture_started and not capture_stopped):
            color = COLORS["red"]
            self.record_button.setText("Stop recording")
        elif event.state in (SessionState.TRANSCRIBING, SessionState.REFINING):
            color = COLORS["amber"]
            self.record_button.setText("Start recording")
            self.record_button.setEnabled(self.engine.can_start_recording)
        elif event.state is SessionState.ERROR:
            color = COLORS["red"]
            self.record_button.setText("Start recording")
            self.record_button.setEnabled(True)
        else:
            self.record_button.setText("Start recording")
            self.record_button.setEnabled(True)
        message = event.message
        if event.warning:
            message = f"{message} {event.warning}".strip()
        self._set_status(message, color)
        text = event.final_text or event.raw_text
        if event.partial_insertion:
            self.latest_text = event.final_text.rstrip()
            self.latest_raw_text = ""
            self.result_text.setPlainText(self.latest_text)
            self.raw_text.clear()
            self.diff_text.clear()
            self.copy_button.setEnabled(bool(self.latest_text))
        elif text:
            self.latest_text = text.rstrip()
            self.result_text.setPlainText(self.latest_text)
            self.copy_button.setEnabled(not event.is_partial)
        if event.raw_text:
            self.latest_raw_text = event.raw_text.strip()
            self.raw_text.setPlainText(self.latest_raw_text)
        if event.raw_text or event.final_text:
            self._refresh_diff()
        if event.inserted or event.state is SessionState.INSERTED:
            self.undo_button.setEnabled(True)
        self.review_action_band.setVisible(
            bool(self.latest_text) or event.state is SessionState.ERROR
        )
        self.result_text.setReadOnly(not event.review_pending)
        self._set_review_actions(event.review_pending)
        if event.state is SessionState.ERROR:
            self.retry_button.setEnabled(True)
        if event.review_pending:
            self.record_button.setEnabled(False)
        if event.state in (SessionState.COMPLETE, SessionState.ERROR):
            self.level_bar.setValue(0)
            self._refresh_recovery()
            self._refresh_history(
                update_dictate=(
                    event.state is SessionState.COMPLETE
                    and not event.review_pending
                    and not event.partial_insertion
                )
            )
        self._handle_evaluation_event(event)

    def _handle_level(self, value: float) -> None:
        self.level_bar.setValue(round(value * 100))
        self.echo_bar.set_level(value)

    def _copy_latest(self) -> None:
        if self.engine.has_pending_review:
            self._complete_review("copied")
            return
        if not self.latest_text:
            return
        self._stage_private_text(self.latest_text)

    def _stage_private_text(self, text: str) -> None:
        status = self.private_text_buffer.stage(text)
        message = f"Manuscript private paste: {status.character_count} characters staged in memory"
        self.private_paste_state.setText(message)
        self.private_paste_state.setAccessibleDescription(message)
        self.clear_private_paste_button.setEnabled(True)
        self._set_status(
            "Staged for Manuscript private paste. Windows clipboard unchanged.",
            COLORS["green"],
        )

    def _clear_private_text(self) -> None:
        self.private_text_buffer.clear()
        message = "Manuscript private paste: empty"
        self.private_paste_state.setText(message)
        self.private_paste_state.setAccessibleDescription(message)
        self.clear_private_paste_button.setEnabled(False)
        self._set_status("Manuscript private paste cleared.", COLORS["green"])

    def _paste_private_text(self) -> None:
        text = self.private_text_buffer.peek()
        if text is None:
            self._set_status("Manuscript private paste is empty.", COLORS["amber"])
            return
        try:
            record = self.private_paste_inserter.insert(text)
        except RuntimeError as exc:
            self._set_status(str(exc), COLORS["amber"])
            return
        if record is None:
            self._set_status("Private paste target changed. Nothing was inserted.", COLORS["amber"])
            return
        self._set_status(
            "Manuscript private text pasted. Windows clipboard unchanged.",
            COLORS["green"],
        )

    def _set_review_actions(self, enabled: bool) -> None:
        for button in (
            self.accept_button,
            self.insert_button,
            self.retry_button,
            self.reject_button,
        ):
            button.setEnabled(enabled)

    def _refresh_diff(self) -> None:
        raw = self.latest_raw_text.split()
        corrected = self.result_text.toPlainText().strip().split()
        changes = [line for line in ndiff(raw, corrected) if not line.startswith("  ")]
        self.diff_text.setPlainText("\n".join(changes) if changes else "No text changes.")

    def _complete_review(self, decision: str) -> None:
        text = self.result_text.toPlainText().strip()
        proposals = self.engine.learning_proposals_for_review(text, decision=decision)
        try:
            if not self.engine.complete_review(text, decision=decision):
                return
        except (RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, "Review", str(exc))
            return
        self.latest_text = text
        if decision == "copied":
            self._stage_private_text(text)
        self.result_text.setReadOnly(True)
        self._set_review_actions(False)
        if proposals:
            self._offer_learning(proposals)

    def _offer_learning(self, proposals) -> None:
        dialog = LearningProposalDialog(proposals, self)
        if not dialog.exec():
            return
        selected = dialog.selected_proposals()
        try:
            for proposal in selected:
                self.store.add_learned_correction(
                    proposal.source,
                    proposal.replacement,
                    replace_existing=True,
                )
        except ValueError as exc:
            QMessageBox.warning(self, "Learned corrections", str(exc))
            return
        if selected:
            self._set_status(
                f"Saved {len(selected)} local learned correction(s).",
                COLORS["green"],
            )

    def _retry_review(self) -> None:
        try:
            started = self.engine.retry_last_recording()
        except (RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, "Review", str(exc))
            return
        if not started:
            self._set_status("Retry could not start the microphone.", COLORS["amber"])

    def _add_snippet(self) -> None:
        try:
            self.store.add_snippet(
                self.snippet_trigger_edit.text(),
                self.snippet_content_edit.toPlainText(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Snippets", str(exc))
            return
        self.snippet_trigger_edit.clear()
        self.snippet_content_edit.clear()
        self._refresh_snippets()
        self.snippet_trigger_edit.setFocus()

    def _refresh_snippets(self) -> None:
        rows = self.store.list_snippets()
        self.snippet_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            trigger = QTableWidgetItem(str(row["spoken_form"]))
            trigger.setData(Qt.ItemDataRole.UserRole, int(row["id"]))
            self.snippet_table.setItem(row_index, 0, trigger)
            self.snippet_table.setItem(row_index, 1, QTableWidgetItem(str(row["content"])))

    def _delete_snippet(self) -> None:
        row = self.snippet_table.currentRow()
        if row < 0:
            return
        item = self.snippet_table.item(row, 0)
        if item is None:
            return
        self.store.delete_snippet(int(item.data(Qt.ItemDataRole.UserRole)))
        self._refresh_snippets()

    def _start_evaluation_run(self) -> None:
        active = self.store.get_active_evaluation_run()
        if active is None:
            self.evaluation_run_id = self.store.start_evaluation_run(
                str(self.evaluation_kind.currentData()),
                build_commit(),
            )
        else:
            self.evaluation_run_id = int(active["id"])
        self.evaluation_trial_id = None
        self._refresh_evaluation()

    def _refresh_evaluation(self) -> None:
        if not hasattr(self, "evaluation_progress"):
            return
        run = (
            self.store.get_evaluation_run(self.evaluation_run_id)
            if self.evaluation_run_id is not None
            else self.store.get_active_evaluation_run()
        )
        if run is None:
            self.evaluation_run_id = None
            self.evaluation_progress.setText("No active run")
            self.evaluation_prompt.clear()
            self.evaluation_record_button.setEnabled(False)
            self.evaluation_export_button.setEnabled(False)
            self.evaluation_kind.setEnabled(True)
            self._set_evaluation_actions(False, retry=False)
            return
        self.evaluation_run_id = int(run["id"])
        self.evaluation_kind.setEnabled(False)
        self.evaluation_export_button.setEnabled(True)
        trials = self.store.list_evaluation_trials(self.evaluation_run_id)
        completed = {
            row["case_id"] for row in trials if row["decision"] in {"accepted", "rejected"}
        }
        metrics = summarize_evaluation(trials, len(self.evaluation_cases))
        if len(completed) >= len(self.evaluation_cases):
            if run["status"] != "complete":
                self.store.finish_evaluation_run(self.evaluation_run_id)
            self.evaluation_progress.setText(
                f"Complete: {metrics['correction_free_acceptance_count']}/"
                f"{metrics['completed_case_count']} correction-free"
            )
            self.evaluation_prompt.setPlainText("Run complete. Export the local CSV and JSON evidence.")
            self.evaluation_record_button.setEnabled(False)
            self._set_evaluation_actions(False, retry=False)
            return
        case = next(item for item in self.evaluation_cases if item.id not in completed)
        case_trials = [row for row in trials if row["case_id"] == case.id]
        interrupted = next(
            (row for row in reversed(case_trials) if row["decision"] == "recording"),
            None,
        )
        if interrupted and self.evaluation_trial_id is None:
            self.evaluation_trial_id = int(interrupted["id"])
        self.evaluation_progress.setText(
            f"{str(run['kind']).title()} run {run['id']} - case {len(completed) + 1}/"
            f"{len(self.evaluation_cases)} - {case.id}"
        )
        self.evaluation_prompt.setPlainText(case.prompt)
        self.evaluation_record_button.setEnabled(self.evaluation_trial_id is None)
        self.evaluation_export_button.setEnabled(True)
        if interrupted:
            self.evaluation_output.setPlaceholderText(
                "This attempt was interrupted. Retry it to continue the same case."
            )
            self._set_evaluation_actions(False, retry=True)

    def _current_evaluation_case(self):
        if self.evaluation_run_id is None:
            return None
        trials = self.store.list_evaluation_trials(self.evaluation_run_id)
        completed = {
            row["case_id"] for row in trials if row["decision"] in {"accepted", "rejected"}
        }
        return next((item for item in self.evaluation_cases if item.id not in completed), None)

    def _toggle_evaluation_recording(self) -> None:
        if self.engine.state is SessionState.LISTENING:
            self.engine.stop_recording()
            return
        if self.evaluation_run_id is None:
            self._start_evaluation_run()
        case = self._current_evaluation_case()
        if case is None or self.evaluation_run_id is None:
            return
        self.evaluation_trial_id = self.store.start_evaluation_trial(
            self.evaluation_run_id,
            case.id,
        )
        self.evaluation_original_text = ""
        self.evaluation_output.clear()
        self.evaluation_output.setReadOnly(True)
        self._set_evaluation_actions(False, retry=False)
        if not self.engine.start_recording(insert_result=False):
            self.store.complete_evaluation_trial(self.evaluation_trial_id, "retry")
            self.evaluation_trial_id = None
            self._refresh_evaluation()

    def _handle_evaluation_event(self, event: SessionEvent) -> None:
        if self.evaluation_trial_id is None:
            return
        if event.state is SessionState.LISTENING:
            self.evaluation_record_button.setText("Stop case")
            if event.final_text:
                self.evaluation_output.setPlainText(event.final_text.rstrip())
        elif event.state in (SessionState.TRANSCRIBING, SessionState.REFINING):
            self.evaluation_record_button.setText("Processing locally...")
            self.evaluation_record_button.setEnabled(False)
        elif event.state is SessionState.COMPLETE:
            text = (event.final_text or event.raw_text).rstrip()
            self.evaluation_original_text = text
            self.evaluation_output.setPlainText(text)
            self.evaluation_output.setReadOnly(False)
            self.store.mark_evaluation_output_ready(self.evaluation_trial_id)
            self.evaluation_record_button.setText("Start case")
            self.evaluation_record_button.setEnabled(False)
            self._set_evaluation_actions(bool(text), retry=True)
        elif event.state is SessionState.ERROR:
            text = (event.final_text or event.raw_text).rstrip()
            if text:
                self.evaluation_original_text = text
                self.evaluation_output.setPlainText(text)
                self.evaluation_output.setReadOnly(False)
                self.store.mark_evaluation_output_ready(self.evaluation_trial_id)
            self.evaluation_record_button.setText("Start case")
            self.evaluation_record_button.setEnabled(False)
            self._set_evaluation_actions(bool(text), retry=True)

    def _set_evaluation_actions(self, accept: bool, *, retry: bool) -> None:
        if not hasattr(self, "evaluation_accept_button"):
            return
        self.evaluation_accept_button.setEnabled(accept)
        self.evaluation_reject_button.setEnabled(accept)
        self.evaluation_retry_button.setEnabled(retry)

    def _complete_evaluation_case(self, decision: str) -> None:
        if self.evaluation_trial_id is None:
            return
        text = self.evaluation_output.toPlainText().strip()
        if decision == "accepted" and not text:
            return
        self.store.complete_evaluation_trial(
            self.evaluation_trial_id,
            decision,
            edited=text != self.evaluation_original_text.strip(),
            final_text=text,
            notes=self.evaluation_notes.text(),
        )
        self.evaluation_trial_id = None
        self.evaluation_original_text = ""
        self.evaluation_output.clear()
        self.evaluation_output.setReadOnly(True)
        self.evaluation_notes.clear()
        self._set_evaluation_actions(False, retry=False)
        self._refresh_evaluation()
        run = self.store.get_evaluation_run(self.evaluation_run_id) if self.evaluation_run_id else None
        if run and run["status"] == "complete":
            self._export_evaluation()

    def _export_evaluation(self) -> None:
        if self.evaluation_run_id is None:
            return
        json_path, csv_path = export_evaluation_run(self.store, self.evaluation_run_id)
        self._set_status(f"Saved {csv_path.name} and {json_path.name} locally.", COLORS["green"])

    def _refresh_recovery(self) -> None:
        enabled = self.store.get_settings().keep_recovery_text
        rows = self.store.list_recovery_items() if enabled else []
        self.recovery_state.setText(
            "Corrected text from failed sessions is stored only on this computer. Recovery itself does not store audio."
            if enabled
            else "Recovery is off. Enable it in Settings to keep corrected text after a failed session."
        )
        self.recovery_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            timestamp = str(row["created_at"])
            try:
                stamp = datetime.fromisoformat(timestamp).astimezone().strftime("%b %d, %I:%M %p")
            except ValueError:
                stamp = timestamp
            time_item = QTableWidgetItem(stamp)
            time_item.setData(Qt.ItemDataRole.UserRole, int(row["id"]))
            self.recovery_table.setItem(row_index, 0, time_item)
            status = {
                "recording": "Interrupted",
                "insertion_uncertain": "Check target",
            }.get(str(row["status"]), "Failed")
            status_item = QTableWidgetItem(status)
            status_item.setToolTip(str(row["error_message"] or ""))
            self.recovery_table.setItem(row_index, 1, status_item)
            self.recovery_table.setItem(row_index, 2, QTableWidgetItem(str(row["mode"]).title()))
            self.recovery_table.setItem(row_index, 3, QTableWidgetItem(str(row["final_text"])))

    def _selected_recovery(self) -> tuple[int, str] | None:
        row = self.recovery_table.currentRow()
        if row < 0:
            return None
        id_item = self.recovery_table.item(row, 0)
        text_item = self.recovery_table.item(row, 3)
        if id_item is None or text_item is None:
            return None
        return int(id_item.data(Qt.ItemDataRole.UserRole)), text_item.text()

    def _copy_recovery(self) -> None:
        selected = self._selected_recovery()
        if selected is None:
            return
        self._stage_private_text(selected[1])

    def _delete_recovery(self) -> None:
        selected = self._selected_recovery()
        if selected is None:
            return
        self.store.delete_recovery_item(selected[0])
        self._refresh_recovery()

    def _clear_recovery(self) -> None:
        if self.recovery_table.rowCount() == 0:
            return
        answer = QMessageBox.question(
            self,
            "Clear recovery",
            "Delete all locally saved recovery text?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.store.clear_recovery_items()
            self._refresh_recovery()

    def _undo_last(self) -> None:
        if self.engine.undo_last():
            self.undo_button.setEnabled(False)
            self._set_status("Last insertion undone.", COLORS["green"])
        else:
            self._set_status("Undo was skipped. Return to the same app and try again.", COLORS["amber"])

    def _add_vocabulary(self) -> None:
        try:
            self.store.add_vocabulary(
                self.spoken_edit.text(),
                self.written_edit.text(),
                self.category_combo.currentText(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Vocabulary", str(exc))
            return
        self.spoken_edit.clear()
        self.written_edit.clear()
        self.engine.correction.update_settings(self.engine.settings)
        self._refresh_vocabulary()
        self.spoken_edit.setFocus()

    def _refresh_vocabulary(self) -> None:
        rows = self.store.list_vocabulary()
        self.vocabulary_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            spoken = QTableWidgetItem(str(row["spoken_form"]))
            spoken.setData(Qt.ItemDataRole.UserRole, int(row["id"]))
            self.vocabulary_table.setItem(row_index, 0, spoken)
            self.vocabulary_table.setItem(row_index, 1, QTableWidgetItem(str(row["written_form"])))
            self.vocabulary_table.setItem(row_index, 2, QTableWidgetItem(str(row["category"])))

    def _delete_vocabulary(self) -> None:
        row = self.vocabulary_table.currentRow()
        if row < 0:
            return
        item = self.vocabulary_table.item(row, 0)
        if item is None:
            return
        self.store.delete_vocabulary(int(item.data(Qt.ItemDataRole.UserRole)))
        self._refresh_vocabulary()

    def _refresh_history(self, *, update_dictate: bool = False) -> None:
        settings = self.store.get_settings()
        enabled = settings.keep_history
        if enabled:
            self.store.purge_expired_history(settings.history_retention_days)
        rows = self.store.list_history() if enabled else []
        self.history_state.setText(
            f"Saved on this computer for {settings.history_retention_days} days"
            if enabled
            else "History is off in Settings"
        )
        self.history_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            timestamp = str(row["created_at"])
            try:
                stamp = datetime.fromisoformat(timestamp).astimezone().strftime("%b %d, %I:%M %p")
            except ValueError:
                stamp = timestamp
            values = (
                stamp,
                str(row["mode"]).title(),
                str(row["final_text"]).strip(),
                f"{int(row['latency_ms'])} ms" if row["latency_ms"] is not None else "",
            )
            for column, value in enumerate(values):
                self.history_table.setItem(row_index, column, QTableWidgetItem(value))
        if update_dictate:
            preview = []
            for row in rows[:20]:
                timestamp = str(row["created_at"])
                try:
                    stamp = datetime.fromisoformat(timestamp).astimezone().strftime(
                        "%b %d, %I:%M %p"
                    )
                except ValueError:
                    stamp = timestamp
                preview.extend(
                    (
                        f"{stamp} · {str(row['mode']).title()}",
                        str(row["final_text"]).strip(),
                        "",
                    )
                )
            self.result_text.setReadOnly(True)
            self.result_text.blockSignals(True)
            try:
                self.result_text.setPlainText("\n".join(preview).rstrip())
            finally:
                self.result_text.blockSignals(False)
        self._refresh_speech_logs()

    def _refresh_speech_logs(self) -> None:
        settings = self.store.get_settings()
        rows = self.store.list_speech_improvement_logs()
        if settings.speech_logging_enabled:
            self.speech_log_state.setText(
                "Encrypted on this computer. Actual audio and transcript stages are deleted after "
                f"{max(1, min(15, settings.speech_log_retention_days))} days."
            )
        else:
            self.speech_log_state.setText(
                "New speech logging is off in Settings. Existing unexpired encrypted logs remain below."
            )
        self.speech_log_table.blockSignals(True)
        self.speech_log_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            timestamp = str(row["created_at"])
            try:
                stamp = datetime.fromisoformat(timestamp).astimezone().strftime("%b %d, %I:%M %p")
            except ValueError:
                stamp = timestamp
            analysis = row.get("analysis") or {}
            review = "Wrong periods" if row["punctuation_wrong"] else str(row["review_decision"] or "Not reviewed").replace("_", " ").title()
            values = (
                stamp,
                str(row["mode"]).title(),
                f"{int(row['duration_ms']) / 1000:.1f} s",
                str(analysis.get("summary") or "No analysis"),
                review,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, int(row["id"]))
                self.speech_log_table.setItem(row_index, column, item)
        self.speech_log_table.blockSignals(False)
        if rows:
            self.speech_log_table.selectRow(0)
        else:
            self._clear_speech_log_details()

    def _selected_speech_log_id(self) -> int | None:
        row = self.speech_log_table.currentRow()
        item = self.speech_log_table.item(row, 0) if row >= 0 else None
        return int(item.data(Qt.ItemDataRole.UserRole)) if item is not None else None

    def _show_selected_speech_log(self) -> None:
        item_id = self._selected_speech_log_id()
        record = self.store.get_speech_improvement_log(item_id) if item_id is not None else None
        if record is None:
            self._clear_speech_log_details()
            return
        self.speech_raw_text.setPlainText(str(record["raw_text"]))
        self.speech_cleanup_text.setPlainText(str(record["deterministic_text"]))
        self.speech_final_text.setPlainText(str(record["final_text"]))
        self.speech_analysis_text.setPlainText(
            format_punctuation_analysis(record.get("analysis") or {})
        )
        self.speech_punctuation_wrong.setChecked(bool(record["punctuation_wrong"]))
        self.speech_notes.setText(str(record["user_notes"]))
        self.speech_play_button.setEnabled(True)
        self.speech_save_button.setEnabled(True)

    def _clear_speech_log_details(self) -> None:
        for editor in (
            self.speech_raw_text,
            self.speech_cleanup_text,
            self.speech_final_text,
            self.speech_analysis_text,
        ):
            editor.clear()
        self.speech_punctuation_wrong.setChecked(False)
        self.speech_notes.clear()
        self.speech_play_button.setEnabled(False)
        self.speech_save_button.setEnabled(False)

    def _play_selected_speech_log(self) -> None:
        item_id = self._selected_speech_log_id()
        record = self.store.get_speech_improvement_log(item_id) if item_id is not None else None
        if record is None:
            return
        try:
            play_pcm16(
                record["audio_pcm"],
                int(record["sample_rate"]),
                self.store.get_settings().audio_output_index,
            )
        except AudioCaptureError as exc:
            QMessageBox.warning(self, "Speech log playback", str(exc))

    def _save_speech_log_review(self) -> None:
        item_id = self._selected_speech_log_id()
        record = self.store.get_speech_improvement_log(item_id) if item_id is not None else None
        if record is None:
            return
        final_text = self.speech_final_text.toPlainText().strip()
        analysis = analyze_punctuation(
            str(record["raw_text"]),
            str(record["deterministic_text"]),
            str(record["suggested_text"]),
            final_text,
        )
        self.store.update_speech_improvement_log(
            item_id,
            final_text=final_text,
            review_decision="user_reviewed",
            punctuation_wrong=self.speech_punctuation_wrong.isChecked(),
            user_notes=self.speech_notes.text().strip(),
            analysis=analysis,
        )
        self._refresh_speech_logs()
        self._set_status("Speech log review saved locally.", COLORS["green"])

    def _delete_selected_speech_log(self) -> None:
        item_id = self._selected_speech_log_id()
        if item_id is None:
            return
        stop_audio_playback()
        self.store.delete_speech_improvement_log(item_id)
        self._refresh_speech_logs()

    def _clear_speech_logs(self) -> None:
        if self.speech_log_table.rowCount() == 0:
            return
        answer = QMessageBox.question(
            self,
            "Clear speech logs",
            "Delete all encrypted speech improvement logs from this computer?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            stop_audio_playback()
            self.store.clear_speech_improvement_logs()
            self._refresh_speech_logs()

    def _purge_expired_speech_logs(self) -> None:
        removed = self.store.purge_expired_speech_improvement_logs()
        if removed:
            self._refresh_speech_logs()

    def _clear_history(self) -> None:
        if self.history_table.rowCount() == 0:
            return
        answer = QMessageBox.question(
            self,
            "Clear history",
            "Delete all saved dictation history from this computer?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.store.clear_history()
            self._refresh_history(update_dictate=True)

    def _tab_changed(self, index: int) -> None:
        name = self.tabs.tabText(index)
        if name == "Documents":
            self.document_panel.refresh_documents()
        elif name == "Drafts":
            self.drafts_panel.refresh_local_status()
        elif name == "Vocabulary":
            self._refresh_vocabulary()
        elif name == "Snippets":
            self._refresh_snippets()
        elif name == "Recovery":
            self._refresh_recovery()
        elif name == "History":
            self._refresh_history()

    def _sync_section_navigation(self, index: int) -> None:
        if self.section_nav.currentRow() == index:
            return
        self.section_nav.blockSignals(True)
        self.section_nav.setCurrentRow(index)
        self.section_nav.blockSignals(False)

    def _show_tab(self, name: str) -> None:
        for index in range(self.tabs.count()):
            if self.tabs.tabText(index) == name:
                self.tabs.setCurrentIndex(index)
                return

    def _open_settings(self) -> None:
        old_hotkey = self.engine.settings.hotkey
        old_hands_free = self.engine.settings.hands_free_hotkey
        old_command_hotkey = self.engine.settings.command_hotkey
        old_automatic_writing_hud = self.engine.settings.automatic_writing_hud
        old_private_paste_hotkey = self.engine.settings.private_paste_hotkey
        old_hands_free_enabled = self.engine.settings.hands_free_enabled
        dialog = SettingsDialog(self.store, self.engine, self)
        if not dialog.exec():
            return
        try:
            configure_startup(dialog.settings.start_with_windows)
        except OSError as exc:
            QMessageBox.warning(self, "Windows startup", f"Settings were saved, but startup could not be changed: {exc}")
        self.engine.update_settings(dialog.settings)
        self.echo_bar.set_saved_position(dialog.settings.echo_bar_x, dialog.settings.echo_bar_y)
        self._set_mode(dialog.settings.mode, save=False)
        self.review_check.blockSignals(True)
        self.review_check.setChecked(dialog.settings.review_before_insert)
        self.review_check.blockSignals(False)
        if (
            dialog.settings.hotkey != old_hotkey
            or dialog.settings.hands_free_hotkey != old_hands_free
            or dialog.settings.command_hotkey != old_command_hotkey
            or dialog.settings.private_paste_hotkey != old_private_paste_hotkey
            or dialog.settings.hands_free_enabled != old_hands_free_enabled
        ):
            self._start_hotkey()
        if dialog.settings.automatic_writing_hud != old_automatic_writing_hud:
            self._start_selection_monitor()
        self._show_ready_state()
        self._refresh_recovery()
        self._refresh_history(update_dictate=True)

    def _start_hotkey(self) -> None:
        for listener in (
            self.hotkey,
            self.hands_free_hotkey,
            self.cancel_hotkey,
            self.command_hotkey,
            self.private_paste_hotkey,
        ):
            if listener is not None:
                listener.stop()
                listener.join(timeout=1.0)
        self.hotkey = None
        self.hands_free_hotkey = None
        self.cancel_hotkey = None
        self.command_hotkey = None
        self.private_paste_hotkey = None
        if not self.enable_hotkey:
            self.hotkey_label.setText("Global hotkey disabled for this run")
            return
        try:
            self.hotkey = GlobalPushToTalk(
                self.engine.settings.hotkey,
                self._capture_dictation_hotkey_target,
                self.bridge.hotkey_released.emit,
                self.bridge.hotkey_error.emit,
            )
            self.hotkey.start()
            self.hotkey.wait_until_ready(timeout=0.75)
            logger.info("Dictation hotkey registered=%s", self.hotkey.registered)
            self.command_hotkey = GlobalPushToTalk(
                self.engine.settings.command_hotkey,
                lambda: None,
                self.bridge.writing_command_pressed.emit,
                lambda message: self.bridge.hotkey_error.emit(f"Selected text: {message}"),
            )
            self.command_hotkey.start()
            self.command_hotkey.wait_until_ready(timeout=0.75)
            logger.info("Selected-text hotkey registered=%s", self.command_hotkey.registered)
            self.private_paste_hotkey = GlobalPushToTalk(
                self.engine.settings.private_paste_hotkey,
                lambda: None,
                self.bridge.private_paste_pressed.emit,
                lambda message: self.bridge.hotkey_error.emit(f"Private paste: {message}"),
            )
            self.private_paste_hotkey.start()
            self.private_paste_hotkey.wait_until_ready(timeout=0.75)
            logger.info("Private paste hotkey registered=%s", self.private_paste_hotkey.registered)
            if self.engine.settings.hands_free_enabled:
                self.hands_free_hotkey = GlobalPushToTalk(
                    self.engine.settings.hands_free_hotkey,
                    self.bridge.hands_free_pressed.emit,
                    lambda: None,
                    lambda message: self.bridge.hotkey_error.emit(f"Hands-free: {message}"),
                )
                self.hands_free_hotkey.start()
                self.hands_free_hotkey.wait_until_ready(timeout=0.75)
                logger.info("Hands-free hotkey registered=%s", self.hands_free_hotkey.registered)
                self.cancel_hotkey = GlobalPushToTalk(
                    "escape",
                    self.bridge.hands_free_cancel.emit,
                    lambda: None,
                    lambda message: self.bridge.hotkey_error.emit(f"Cancel key: {message}"),
                    allow_single_key=True,
                )
                self.cancel_hotkey.start()
                self.cancel_hotkey.wait_until_ready(timeout=0.75)
        except ValueError as exc:
            self._show_hotkey_error(str(exc))

    def _show_hotkey_error(self, message: str) -> None:
        self._set_status(message, COLORS["amber"])

    def _start_selection_monitor(self) -> None:
        if self.selection_monitor is not None:
            self.selection_monitor.stop(timeout=1.0)
            self.selection_monitor = None
        if (
            not self.enable_hotkey
            or not self.engine.settings.automatic_writing_hud
            or os.environ.get("ECHOLINK_DISABLE_AUTO_HUD") == "1"
        ):
            return
        monitor = WindowsSelectionMonitor(
            WindowsSelectionReader(
                max_characters=500_000,
                include_caret_sentence=True,
                include_word_page=True,
            ),
            self.bridge.automatic_selection_detected.emit,
            cleared_callback=self.bridge.automatic_selection_cleared.emit,
            poll_interval_seconds=0.15,
            stable_seconds=0.30,
        )
        monitor.start()
        self.selection_monitor = monitor
        logger.info("Automatic Writing HUD selection monitor started=%s", monitor.running)

    def _pause_selection_monitor(self) -> None:
        if self.selection_monitor is not None:
            self.selection_monitor.pause()

    def _resume_selection_monitor(self) -> None:
        if self.selection_monitor is not None:
            self.selection_monitor.resume()

    def _save_badge_offset(self, x: int, y: int) -> None:
        """Persist where the writer dragged the badge, relative to the text.

        Written on every drop rather than behind a preference, because the
        preference IS the drop: "the system should remember wherever you move
        it in relation to the text AUTOMATICALLY".
        """
        settings = self.store.get_settings()
        settings.hud_badge_offset_x = int(x)
        settings.hud_badge_offset_y = int(y)
        self.store.save_settings(settings)
        self.engine.settings.hud_badge_offset_x = int(x)
        self.engine.settings.hud_badge_offset_y = int(y)

    def _save_echo_bar_position(self, x: int, y: int) -> None:
        settings = self.store.get_settings()
        settings.echo_bar_x = int(x)
        settings.echo_bar_y = int(y)
        self.store.save_settings(settings)
        self.engine.settings.echo_bar_x = int(x)
        self.engine.settings.echo_bar_y = int(y)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.tray_enabled and self.engine.settings.close_to_tray and not self._quitting:
            self.hide()
            self.echo_bar.close()
            event.ignore()
            return
        if not self.document_panel.confirm_close():
            self._quitting = False
            event.ignore()
            return
        self.shutdown()
        event.accept()

    def shutdown(self) -> None:
        if self._shutdown_complete:
            return
        self._shutdown_complete = True
        self._warm_retry_stop.set()
        with self._palette_ai_lock:
            self._palette_ai_shutdown = True
            self._palette_ai_queue.clear()
        self.speech_log_purge_timer.stop()
        self.palette_focus_timer.stop()
        stop_audio_playback()
        if self.selection_monitor is not None:
            self.selection_monitor.stop(timeout=1.0)
            self.selection_monitor = None
        for listener in (
            self.hotkey,
            self.hands_free_hotkey,
            self.cancel_hotkey,
            self.command_hotkey,
            self.private_paste_hotkey,
        ):
            if listener is not None:
                listener.stop()
                listener.join(timeout=1.0)
        self.private_text_buffer.clear()
        self.engine.shutdown()
        self.ai_coordinator.close()
        self.media_pause.shutdown()
        self.echo_bar.close()
        self.target_underline.close()
        self.rewrite_palette.close()
        if self.tray_icon is not None:
            self.tray_icon.hide()
