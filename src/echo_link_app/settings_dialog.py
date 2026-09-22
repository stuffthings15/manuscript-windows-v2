from __future__ import annotations

import logging
import threading
from dataclasses import replace
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from echo_link_app.learning_dialog import LearningManagerDialog
from echo_link_app.accessibility import bind_form_labels, set_accessible, update_status_label
from echo_link_app.sound_cues import (
    BUILTIN_START,
    BUILTIN_STOP,
    list_audio_outputs,
    list_sound_choices,
    play_cue,
)
from echo_link_core.audio import list_microphones
from echo_link_core.data_folder_acl import DataFolderProtectionError, harden_windows_data_folder
from echo_link_core.hotkeys import hotkeys_overlap, parse_hotkey
from echo_link_core.model_packs import ModelPackError, import_model_pack, remove_model_pack
from echo_link_core.models import (
    DictationMode,
    DictationSettings,
    InsertionMode,
    OLLAMA_IDLE_UNLOAD_SECONDS,
)
from echo_link_core.polish_hardware import HardwareDetector, HardwarePolicyManager
from echo_link_core.polish_types import PolishMode, RuntimeBackend
from echo_link_core.polish_model_packs import ModelPackManager
from echo_link_core.polish_runtime_packs import RuntimePackManager
from echo_link_core.paths import data_root, log_path, user_model_root
from echo_link_core.recommendation import local_hardware_profile, recommend_local_speech
from echo_link_core.transcription import LocalWhisperTranscriber, discover_local_models, local_model_path


class SettingsDialog(QDialog):
    PURGE_CONFIRMATION = "DELETE ALL PRIVATE DATA"
    model_status_ready = Signal(str)
    speed_check_ready = Signal(str)
    model_pack_ready = Signal(object)

    def __init__(self, store, engine, parent=None):
        super().__init__(parent)
        self.store = store
        self.engine = engine
        self.settings = store.get_settings()
        self.echo_bar_position = (self.settings.echo_bar_x, self.settings.echo_bar_y)
        self.setWindowTitle("Manuscript Settings")
        self.setMinimumSize(660, 520)
        self.resize(700, 680)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        title = QLabel("Settings")
        title.setObjectName("appTitle")
        title.setFixedHeight(max(40, title.sizeHint().height()))
        root.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(2, 2, 8, 2)
        content_layout.setSpacing(12)

        input_group = QGroupBox("Input")
        input_form = QFormLayout(input_group)
        input_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        input_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.microphone_combo = QComboBox()
        self.microphone_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.microphone_combo.setMinimumContentsLength(24)
        self.microphone_combo.addItem("System default", None)
        for microphone in list_microphones():
            self.microphone_combo.addItem(microphone.name, microphone.index)
        # Select by name where one was stored, so reopening Settings shows the
        # microphone actually in use rather than whatever now holds its index.
        saved_name = (self.settings.microphone_name or "").strip()
        by_name = (
            self.microphone_combo.findText(saved_name) if saved_name else -1
        )
        if by_name >= 0:
            self.microphone_combo.setCurrentIndex(by_name)
        elif saved_name:
            self.microphone_combo.setCurrentIndex(0)  # named device not connected
        else:
            _select_data(self.microphone_combo, self.settings.microphone_index)
        input_form.addRow("Microphone", self.microphone_combo)

        self.hotkey_edit = QLineEdit(self.settings.hotkey)
        self.hotkey_edit.setPlaceholderText("ctrl+win")
        input_form.addRow("Global dictation hotkey", self.hotkey_edit)

        self.hands_free_check = QCheckBox("Enable hands-free toggle dictation")
        self.hands_free_check.setChecked(self.settings.hands_free_enabled)
        input_form.addRow("", self.hands_free_check)
        self.hands_free_hotkey_edit = QLineEdit(self.settings.hands_free_hotkey)
        self.hands_free_hotkey_edit.setPlaceholderText("ctrl+alt+space")
        input_form.addRow("Hands-free hotkey", self.hands_free_hotkey_edit)
        self.command_hotkey_edit = QLineEdit(self.settings.command_hotkey)
        self.command_hotkey_edit.setPlaceholderText("ctrl+shift+space")
        input_form.addRow("Writing HUD hotkey", self.command_hotkey_edit)
        self.private_paste_hotkey_edit = QLineEdit(self.settings.private_paste_hotkey)
        self.private_paste_hotkey_edit.setPlaceholderText("ctrl+alt+v")
        self.private_paste_hotkey_edit.setToolTip(
            "Pastes Manuscript's memory-only buffer with Direct input. "
            "It never uses the Windows clipboard."
        )
        input_form.addRow("Manuscript private paste hotkey", self.private_paste_hotkey_edit)
        self.silence_stop_check = QCheckBox("Stop hands-free dictation after silence")
        self.silence_stop_check.setChecked(self.settings.silence_auto_stop_enabled)
        input_form.addRow("", self.silence_stop_check)
        self.silence_timeout_spin = QDoubleSpinBox()
        self.silence_timeout_spin.setRange(1.0, 10.0)
        self.silence_timeout_spin.setSingleStep(0.5)
        self.silence_timeout_spin.setDecimals(1)
        self.silence_timeout_spin.setSuffix(" seconds")
        self.silence_timeout_spin.setValue(self.settings.silence_timeout_seconds)
        input_form.addRow("Silence timeout", self.silence_timeout_spin)
        cancel_hint = QLabel("Press Esc to discard an active hands-free recording.")
        cancel_hint.setObjectName("mutedLabel")
        cancel_hint.setWordWrap(True)
        input_form.addRow("Emergency cancel", cancel_hint)
        self.hands_free_check.toggled.connect(self.hands_free_hotkey_edit.setEnabled)
        self.hands_free_check.toggled.connect(self.silence_stop_check.setEnabled)
        self.hands_free_check.toggled.connect(self._sync_silence_controls)
        self.silence_stop_check.toggled.connect(self._sync_silence_controls)
        self.hands_free_hotkey_edit.setEnabled(self.hands_free_check.isChecked())
        self.silence_stop_check.setEnabled(self.hands_free_check.isChecked())
        self._sync_silence_controls()

        self.language_combo = QComboBox()
        self.language_combo.addItem("Automatic detection", "auto")
        self.language_combo.addItem("English", "en")
        self.language_combo.addItem("Spanish", "es")
        self.language_combo.addItem("French", "fr")
        self.language_combo.addItem("German", "de")
        self.language_combo.addItem("Italian", "it")
        self.language_combo.addItem("Portuguese", "pt")
        self.language_combo.addItem("Chinese", "zh")
        self.language_combo.addItem("Japanese", "ja")
        self.language_combo.addItem("Korean", "ko")
        self.language_combo.addItem("Hindi", "hi")
        _select_data(self.language_combo, self.settings.language)
        input_form.addRow("Language", self.language_combo)
        self.streaming_check = QCheckBox("Show live local text while speaking")
        self.streaming_check.setChecked(self.settings.streaming_enabled)
        input_form.addRow("", self.streaming_check)
        self.whisper_check = QCheckBox("Whisper mode for quiet speech")
        self.whisper_check.setChecked(self.settings.whisper_mode)
        input_form.addRow("", self.whisper_check)
        self.echo_bar_check = QCheckBox("Show the Manuscript Bar while dictating")
        self.echo_bar_check.setChecked(self.settings.show_echo_bar)
        input_form.addRow("", self.echo_bar_check)
        echo_bar_row = QHBoxLayout()
        self.echo_bar_position_label = QLabel(
            "Saved custom position" if self.settings.echo_bar_x is not None else "Top center"
        )
        self.echo_bar_position_label.setObjectName("mutedLabel")
        echo_bar_row.addWidget(self.echo_bar_position_label, 1)
        reset_echo_bar = QPushButton("Reset")
        reset_echo_bar.clicked.connect(self._reset_echo_bar_position)
        echo_bar_row.addWidget(reset_echo_bar)
        input_form.addRow("Manuscript Bar position", echo_bar_row)
        self.pause_media_check = QCheckBox("Pause playing media while listening")
        self.pause_media_check.setChecked(self.settings.pause_media_during_dictation)
        self.pause_media_check.setToolTip("Resumes only media sessions Manuscript paused.")
        input_form.addRow("", self.pause_media_check)
        self.sound_check = QCheckBox("Play start and stop sounds")
        self.sound_check.setChecked(self.settings.sound_enabled)
        input_form.addRow("", self.sound_check)
        self.pre_roll_spin = QSpinBox()
        self.pre_roll_spin.setRange(0, 2000)
        self.pre_roll_spin.setSingleStep(100)
        self.pre_roll_spin.setSuffix(" ms")
        self.pre_roll_spin.setValue(self.settings.pre_roll_ms)
        self.pre_roll_spin.setToolTip(
            "Memory-only lead-in. It is saved only when encrypted speech improvement logs are on."
        )
        input_form.addRow("Protect first words", self.pre_roll_spin)
        self.max_recording_spin = QSpinBox()
        self.max_recording_spin.setRange(10, 900)
        self.max_recording_spin.setSuffix(" seconds")
        self.max_recording_spin.setValue(self.settings.max_recording_seconds)
        input_form.addRow("Maximum recording", self.max_recording_spin)
        content_layout.addWidget(input_group)

        self.sound_group = QGroupBox("Sound Cues")
        sound_form = QFormLayout(self.sound_group)
        sound_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        sound_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        sound_choices = list_sound_choices()
        self.start_sound_combo = self._cue_combo(sound_choices, self.settings.start_sound)
        start_row = QHBoxLayout()
        start_row.addWidget(self.start_sound_combo, 1)
        start_browse = QPushButton("Browse")
        start_browse.clicked.connect(lambda: self._browse_cue(self.start_sound_combo))
        start_test = QPushButton("Test")
        start_test.clicked.connect(lambda: self._test_cue(self.start_sound_combo, BUILTIN_START))
        start_row.addWidget(start_browse)
        start_row.addWidget(start_test)
        sound_form.addRow("Start cue", start_row)

        self.stop_sound_combo = self._cue_combo(sound_choices, self.settings.stop_sound)
        stop_row = QHBoxLayout()
        stop_row.addWidget(self.stop_sound_combo, 1)
        stop_browse = QPushButton("Browse")
        stop_browse.clicked.connect(lambda: self._browse_cue(self.stop_sound_combo))
        stop_test = QPushButton("Test")
        stop_test.clicked.connect(lambda: self._test_cue(self.stop_sound_combo, BUILTIN_STOP))
        stop_row.addWidget(stop_browse)
        stop_row.addWidget(stop_test)
        sound_form.addRow("Stop cue", stop_row)

        self.output_combo = QComboBox()
        self.output_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.output_combo.setMinimumContentsLength(24)
        self.output_combo.addItem("System default", None)
        for output in list_audio_outputs():
            self.output_combo.addItem(output.name, output.index)
        _select_data(self.output_combo, self.settings.audio_output_index)
        sound_form.addRow("Play through", self.output_combo)

        self.bluetooth_delay_combo = QComboBox()
        for label, value in (
            ("Auto - detect Bluetooth", -1),
            ("Off", 0),
            ("500 ms", 500),
            ("700 ms", 700),
            ("1000 ms", 1000),
            ("1500 ms", 1500),
            ("2000 ms", 2000),
        ):
            self.bluetooth_delay_combo.addItem(label, value)
        _select_data(self.bluetooth_delay_combo, self.settings.bluetooth_delay_ms)
        self.bluetooth_delay_combo.setToolTip("Wakes sleeping wireless speakers before the start cue.")
        sound_form.addRow("Speaker wake delay", self.bluetooth_delay_combo)
        self.sound_group.setEnabled(self.sound_check.isChecked())
        self.sound_check.toggled.connect(self.sound_group.setEnabled)
        content_layout.addWidget(self.sound_group)

        writing_group = QGroupBox("Writing")
        writing_form = QFormLayout(writing_group)
        writing_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        writing_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Fast", DictationMode.FAST.value)
        self.mode_combo.addItem("Developer", DictationMode.DEVELOPER.value)
        self.mode_combo.addItem("Refine", DictationMode.REFINE.value)
        self.mode_combo.addItem("Accuracy", DictationMode.ACCURACY.value)
        _select_data(self.mode_combo, self.settings.mode.value)
        writing_form.addRow("Default mode", self.mode_combo)
        self.insert_check = QCheckBox("Insert text into the active Windows application")
        self.insert_check.setChecked(self.settings.insert_text)
        writing_form.addRow("", self.insert_check)
        self.insertion_mode_combo = QComboBox()
        self.insertion_mode_combo.addItem(
            "Direct Unicode (BMP) - no Windows clipboard",
            InsertionMode.DIRECT.value,
        )
        self.insertion_mode_combo.addItem(
            "Compatibility: Windows clipboard, then Direct fallback",
            InsertionMode.AUTO.value,
        )
        self.insertion_mode_combo.addItem(
            "Compatibility: Windows clipboard only",
            InsertionMode.CLIPBOARD.value,
        )
        _select_data(self.insertion_mode_combo, self.settings.insertion_mode.value)
        self.insertion_mode_combo.setToolTip(
            "Direct never reads or writes the Windows clipboard. Compatibility modes temporarily "
            "replace it, request exclusion from Windows Clipboard History and Cloud Clipboard, "
            "then try to restore it. Direct cannot safely send emoji. Windows still blocks "
            "elevated apps unless Manuscript runs at the same access level."
        )
        writing_form.addRow("Insertion method", self.insertion_mode_combo)
        self.review_check = QCheckBox("Review text before inserting it")
        self.review_check.setChecked(self.settings.review_before_insert)
        writing_form.addRow("", self.review_check)
        self.automatic_writing_hud_check = QCheckBox(
            "Show the Writing HUD automatically when text is selected"
        )
        self.automatic_writing_hud_check.setChecked(self.settings.automatic_writing_hud)
        self.automatic_writing_hud_check.setToolTip(
            "Uses Windows accessibility data to detect a stable selection. "
            "Automatic detection does not read or change the Windows clipboard."
        )
        writing_form.addRow("", self.automatic_writing_hud_check)
        self.trailing_check = QCheckBox("Add a trailing space after dictation")
        self.trailing_check.setChecked(self.settings.append_trailing_space)
        writing_form.addRow("", self.trailing_check)
        content_layout.addWidget(writing_group)

        polish_group = QGroupBox("Writing AI")
        polish_form = QFormLayout(polish_group)
        polish_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        polish_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        polish_help = QLabel(
            "English corrections start with fast local checks. AI handles harder "
            "grammar, spelling, structure, and tone work."
        )
        polish_help.setWordWrap(True)
        polish_help.setObjectName("mutedLabel")
        polish_form.addRow(polish_help)
        self.polish_mode_combo = QComboBox()
        self.polish_mode_combo.addItem(
            "Auto - choose rules, sentence AI, or paragraph AI",
            PolishMode.AUTO.value,
        )
        self.polish_mode_combo.addItem(
            "Instant - rules first; AI only for severe sentence errors",
            PolishMode.INSTANT.value,
        )
        self.polish_mode_combo.addItem(
            "Balanced - AI for confirmed sentence and short-paragraph errors",
            PolishMode.BALANCED.value,
        )
        self.polish_mode_combo.addItem(
            "Professional - best local AI for manual document polish",
            PolishMode.PROFESSIONAL.value,
        )
        _select_data(self.polish_mode_combo, self.settings.polish_mode.value)
        polish_form.addRow("Polish mode", self.polish_mode_combo)
        self.polish_pack_label = QLabel(
            _polish_pack_summary(self.store, self.settings.polish_model_pack_id)
        )
        self.polish_pack_label.setWordWrap(True)
        self.polish_pack_label.setObjectName("mutedLabel")
        polish_form.addRow("AI setup", self.polish_pack_label)
        self.polish_download_consent_check = QCheckBox(
            "Allow the recommended AI to download to this computer"
        )
        self.polish_download_consent_check.setChecked(
            self.settings.polish_download_consent
        )
        self.polish_download_consent_check.setToolTip(
            "The app verifies signed AI files, exact digests, sizes, and licenses "
            "before use. Correction text is never "
            "downloaded or uploaded."
        )
        polish_form.addRow("", self.polish_download_consent_check)
        polish_privacy = QLabel(
            "No writing or benchmark text is stored. The benchmark cache keeps only "
            "hardware details, speed, and resource measurements."
        )
        polish_privacy.setWordWrap(True)
        polish_privacy.setObjectName("mutedLabel")
        polish_form.addRow("Privacy", polish_privacy)
        content_layout.addWidget(polish_group)

        model_group = QGroupBox("Speech Recognition")
        model_form = QFormLayout(model_group)
        model_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        model_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        model_form.setVerticalSpacing(10)
        speech_model_help = QLabel(
            "Speech stays on this computer. Manuscript selects a local speech AI."
        )
        speech_model_help.setWordWrap(True)
        speech_model_help.setObjectName("mutedLabel")
        model_form.addRow(speech_model_help)
        self.model_combo = QComboBox()
        self.model_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.installed_models = discover_local_models()
        for model in self.installed_models:
            label = "Multilingual speech AI" if model.multilingual else "English speech AI"
            self.model_combo.addItem(label, model.name)
        if self.model_combo.findData(self.settings.model_name) < 0:
            self.model_combo.addItem("Speech AI unavailable", self.settings.model_name)
        _select_data(self.model_combo, self.settings.model_name)
        self.model_combo.currentIndexChanged.connect(self._sync_model_language)
        model_form.addRow("Speech AI", self.model_combo)

        self.model_folder_button = QPushButton("Open AI folder")
        self.model_folder_button.clicked.connect(self._open_model_folder)
        self.model_folder_button.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        model_form.addRow("", self.model_folder_button)
        self.import_model_button = QPushButton("Import AI pack")
        self.import_model_button.clicked.connect(self._start_model_import)
        self.import_model_button.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        model_form.addRow("", self.import_model_button)
        self.remove_model_button = QPushButton("Remove selected AI pack")
        self.remove_model_button.clicked.connect(self._start_model_removal)
        self.remove_model_button.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        model_form.addRow("", self.remove_model_button)
        self.model_pack_ready.connect(self._handle_model_pack_result)
        self.hardware_profile = local_hardware_profile()
        self.device_combo = QComboBox()
        self.device_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.device_combo.addItem("CPU - lower power", "cpu")
        if self.hardware_profile.cuda:
            self.device_combo.addItem("NVIDIA GPU - verified local runtime", "cuda")
        _select_data(self.device_combo, self.settings.device)
        model_form.addRow("Speech device", self.device_combo)
        self.recommendation_label = QLabel()
        self.recommendation_label.setWordWrap(True)
        self.recommendation_label.setObjectName("mutedLabel")
        model_form.addRow("Recommended", self.recommendation_label)
        self.use_recommended_button = QPushButton("Use recommended local setup")
        self.use_recommended_button.clicked.connect(self._use_recommended_setup)
        self.use_recommended_button.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        model_form.addRow("", self.use_recommended_button)
        self.speed_check_button = QPushButton("Run local speed check")
        self.speed_check_button.clicked.connect(self._run_speed_check)
        self.speed_check_ready.connect(self._set_speed_check_result)
        self.speed_check_button.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        model_form.addRow("", self.speed_check_button)

        refinement_group = QGroupBox("Grammar and Punctuation")
        refinement_form = QFormLayout(refinement_group)
        refinement_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        refinement_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        refinement_form.setVerticalSpacing(10)
        refinement_help = QLabel(
            "Optional writing cleanup also runs locally. It never sends dictated text online."
        )
        refinement_help.setWordWrap(True)
        refinement_help.setObjectName("mutedLabel")
        refinement_form.addRow(refinement_help)
        self.refine_check = QCheckBox("Allow local grammar and punctuation refinement")
        self.refine_check.setChecked(self.settings.local_refinement_enabled)
        refinement_form.addRow("", self.refine_check)
        self.document_context_check = QCheckBox(
            "Spell dictated names the way the open document spells them"
        )
        self.document_context_check.setChecked(self.settings.document_context_enabled)
        self.document_context_check.setToolTip(
            "Reads the document you are dictating into so a name you have "
            "already written is not respelled. The text stays on this computer "
            "and is never sent to the local AI."
        )
        refinement_form.addRow("", self.document_context_check)
        accepted_writing_model = (
            self.settings.ollama_model if self.settings.polish_model_pack_id else ""
        )
        self.ollama_model_edit = QLineEdit(accepted_writing_model, self)
        self.ollama_model_edit.setReadOnly(True)
        self.ollama_model_edit.hide()
        self.gpu_release_spin = QSpinBox()
        self.gpu_release_spin.setRange(
            OLLAMA_IDLE_UNLOAD_SECONDS,
            OLLAMA_IDLE_UNLOAD_SECONDS,
        )
        self.gpu_release_spin.setSuffix(" seconds")
        self.gpu_release_spin.setValue(OLLAMA_IDLE_UNLOAD_SECONDS)
        self.gpu_release_spin.setToolTip(
            "Local writing AI releases the GPU after five minutes without a request."
        )
        refinement_form.addRow("Release GPU after", self.gpu_release_spin)

        model_status_group = QGroupBox("Status")
        model_status_layout = QVBoxLayout(model_status_group)
        model_status_layout.setSpacing(10)
        self.model_status = QLabel()
        self.model_status.setWordWrap(True)
        self.model_status.setMinimumHeight(self.model_status.fontMetrics().lineSpacing() * 2)
        self.model_status.setObjectName("mutedLabel")
        self.model_status_ready.connect(self._set_model_status)
        model_status_layout.addWidget(self.model_status)
        self.check_button = QPushButton("Check local AI")
        self.check_button.clicked.connect(self._refresh_model_status)
        self.check_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        model_status_layout.addWidget(self.check_button)
        content_layout.addWidget(model_group)

        app_group = QGroupBox("Windows startup")
        app_form = QFormLayout(app_group)
        app_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.startup_check = QCheckBox("Start Manuscript when I sign in to Windows")
        self.startup_check.setChecked(self.settings.start_with_windows)
        self.startup_check.setToolTip(
            "Starts Manuscript quietly in the system tray after you sign in."
        )
        app_form.addRow("", self.startup_check)
        self.close_to_tray_check = QCheckBox("Keep Manuscript ready in the system tray when closed")
        self.close_to_tray_check.setChecked(self.settings.close_to_tray)
        app_form.addRow("", self.close_to_tray_check)

        privacy_group = QGroupBox("Privacy and local data")
        privacy_form = QFormLayout(privacy_group)
        privacy_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.history_check = QCheckBox("Keep local dictation history")
        self.history_check.setChecked(self.settings.keep_history)
        privacy_form.addRow("", self.history_check)
        self.history_retention_spin = QSpinBox()
        self.history_retention_spin.setRange(1, 365)
        self.history_retention_spin.setSuffix(" days")
        self.history_retention_spin.setValue(self.settings.history_retention_days)
        self.history_retention_spin.setEnabled(self.history_check.isChecked())
        self.history_retention_spin.setToolTip(
            "Older dictations are deleted from this computer automatically."
        )
        self.history_check.toggled.connect(self.history_retention_spin.setEnabled)
        privacy_form.addRow("Keep dictation history for", self.history_retention_spin)
        self.speech_logging_check = QCheckBox("Keep encrypted speech improvement logs")
        self.speech_logging_check.setChecked(self.settings.speech_logging_enabled)
        self.speech_logging_check.setToolTip(
            "Stores actual microphone audio and transcript stages only in Manuscript's encrypted "
            "local database. Logs never leave this computer and expire automatically."
        )
        privacy_form.addRow("", self.speech_logging_check)
        self.speech_retention_spin = QSpinBox()
        self.speech_retention_spin.setRange(1, 15)
        self.speech_retention_spin.setSuffix(" days")
        self.speech_retention_spin.setValue(
            max(1, min(15, int(self.settings.speech_log_retention_days)))
        )
        self.speech_retention_spin.setEnabled(self.speech_logging_check.isChecked())
        self.speech_retention_spin.setToolTip("Maximum is 15 days. Expired logs are deleted locally.")
        self.speech_logging_check.toggled.connect(self.speech_retention_spin.setEnabled)
        privacy_form.addRow("Delete speech logs after", self.speech_retention_spin)
        self.recovery_check = QCheckBox("Keep failed text for local recovery")
        self.recovery_check.setChecked(self.settings.keep_recovery_text)
        self.recovery_check.setToolTip("This recovery feature stores corrected text only.")
        privacy_form.addRow("", self.recovery_check)
        self.learning_check = QCheckBox("Offer approved learned corrections from Review edits")
        self.learning_check.setChecked(self.settings.personal_learning_enabled)
        self.learning_check.setToolTip(
            "This learning feature stores only phrase pairs you explicitly approve."
        )
        privacy_form.addRow("", self.learning_check)
        manage_learning = QPushButton("Manage learned corrections")
        manage_learning.clicked.connect(self._manage_learning)
        privacy_form.addRow("", manage_learning)
        import_button = QPushButton("Import legacy dictation settings")
        import_button.clicked.connect(self._import_legacy_dictation)
        privacy_form.addRow("", import_button)
        self.delete_private_data_button = QPushButton("Delete all private data")
        self.delete_private_data_button.setObjectName("dangerButton")
        self.delete_private_data_button.setAccessibleName("Delete all private Manuscript data")
        self.delete_private_data_button.setToolTip(
            "Deletes local content and resets settings. This is not guaranteed secure erasure."
        )
        self.delete_private_data_button.clicked.connect(self._delete_all_private_data)
        privacy_form.addRow("", self.delete_private_data_button)
        self.harden_data_folder_button = QPushButton("Harden Windows data folder")
        self.harden_data_folder_button.setAccessibleName("Harden Manuscript data folder permissions")
        self.harden_data_folder_button.setToolTip(
            "Removes inherited broad access from Manuscript's private folder and grants full "
            "control to your account, Windows SYSTEM, and local administrators."
        )
        self.harden_data_folder_button.clicked.connect(self._harden_data_folder)
        privacy_form.addRow("", self.harden_data_folder_button)
        content_layout.addWidget(privacy_group)
        content_layout.addStretch(1)

        def category_page(*groups: QGroupBox) -> QScrollArea:
            category_scroll = QScrollArea()
            category_scroll.setWidgetResizable(True)
            category_scroll.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            page = QWidget()
            page.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(4, 6, 10, 6)
            page_layout.setSpacing(12)
            for group in groups:
                page_layout.addWidget(group)
            page_layout.addStretch(1)
            category_scroll.setWidget(page)
            return category_scroll

        self.settings_tabs = QTabWidget()
        self.settings_tabs.setDocumentMode(True)
        self.settings_scrolls = {
            "Input and audio": category_page(input_group, self.sound_group),
            "Writing": category_page(writing_group, polish_group),
            "Local AI": category_page(model_group, refinement_group, model_status_group),
            "App": category_page(app_group),
            "Privacy": category_page(privacy_group),
        }
        for name, category_scroll in self.settings_scrolls.items():
            self.settings_tabs.addTab(category_scroll, name)
            set_accessible(
                category_scroll,
                f"{name} settings",
                f"Scroll vertically through the {name.lower()} settings when needed.",
            )
        root.addWidget(self.settings_tabs, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        set_accessible(self, "Manuscript settings")
        set_accessible(
            self.settings_tabs,
            "Settings categories",
            "Use the category tabs to reach input, writing, local AI, app, and privacy settings.",
        )
        set_accessible(
            self.echo_bar_position_label,
            "Manuscript Bar position status",
            self.echo_bar_position_label.text(),
        )
        set_accessible(
            self.recommendation_label,
            "Recommended local setup",
            self.recommendation_label.text(),
        )
        set_accessible(self.model_status, "Local AI status", self.model_status.text())
        for form in (
            input_form,
            sound_form,
            writing_form,
            polish_form,
            model_form,
            refinement_form,
            app_form,
            privacy_form,
        ):
            bind_form_labels(form)
        self._sync_model_language()
        self.language_combo.currentIndexChanged.connect(self._refresh_recommendation)
        self._refresh_recommendation()
        self._refresh_model_status()
        self.hotkey_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        first_scroll = self.settings_scrolls["Input and audio"]
        QTimer.singleShot(0, lambda: first_scroll.verticalScrollBar().setValue(0))

    def _refresh_model_status(self) -> None:
        if not self.check_button.isEnabled():
            return
        self.check_button.setEnabled(False)
        self.model_status.setText("Checking local AI...")
        speech_model = str(self.model_combo.currentData() or self.settings.model_name)
        threading.Thread(
            target=self._collect_model_status,
            args=(speech_model,),
            daemon=True,
            name="echo-link-model-status",
        ).start()

    def _sync_silence_controls(self) -> None:
        self.silence_timeout_spin.setEnabled(
            self.hands_free_check.isChecked() and self.silence_stop_check.isChecked()
        )

    def _refresh_recommendation(self) -> None:
        recommendation = recommend_local_speech(
            self.installed_models,
            language=str(self.language_combo.currentData() or "en"),
            profile=self.hardware_profile,
        )
        self.local_recommendation = recommendation
        if recommendation is None:
            self.recommendation_label.setText("Local speech AI is unavailable.")
            self.use_recommended_button.setEnabled(False)
            return
        device = "NVIDIA GPU" if recommendation.device == "cuda" else "CPU"
        self.recommendation_label.setText(f"{device} is recommended for speech on this computer.")
        self.use_recommended_button.setEnabled(True)

    def _use_recommended_setup(self) -> None:
        recommendation = self.local_recommendation
        if recommendation is None:
            return
        _select_data(self.model_combo, recommendation.model_name)
        _select_data(self.device_combo, recommendation.device)
        self.model_status.setText("Recommended local setup selected. Save to apply it.")

    def _run_speed_check(self) -> None:
        if not self.speed_check_button.isEnabled():
            return
        settings = replace(
            self.settings,
            model_name=str(self.model_combo.currentData() or "base.en"),
            device=str(self.device_combo.currentData() or "cpu"),
            compute_type="float16" if self.device_combo.currentData() == "cuda" else "int8",
            language=str(self.language_combo.currentData() or "en"),
        )
        self.speed_check_button.setEnabled(False)
        self.model_status.setText("Running a short local-only speech speed check...")
        threading.Thread(
            target=self._collect_speed_check,
            args=(settings,),
            daemon=True,
            name="echo-link-speed-check",
        ).start()

    def _collect_speed_check(self, settings: DictationSettings) -> None:
        transcriber = LocalWhisperTranscriber(settings)
        try:
            result = transcriber.benchmark()
            factor = float(result["realtime_factor"])
            pace = "faster than real time" if factor <= 1.0 else "slower than real time"
            text = (
                f"Speech AI on {str(result['device']).upper()} processed the "
                f"local sample at {factor:.2f}x real time ({pace}). "
                f"AI load: {float(result['load_seconds']):.2f}s."
            )
        except Exception as exc:
            text = f"Local speed check failed: {exc}"
        finally:
            transcriber.release_model()
        try:
            self.speed_check_ready.emit(text)
        except RuntimeError:
            pass

    def _set_speed_check_result(self, text: str) -> None:
        update_status_label(self.model_status, text, assertive="failed" in text.casefold())
        self.speed_check_button.setEnabled(True)

    @staticmethod
    def _cue_combo(choices: list[str], current: str) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItem("Muted")
        combo.addItems(choices)
        index = combo.findText(current)
        if index >= 0:
            combo.setCurrentIndex(index)
        else:
            combo.setEditText(current)
        return combo

    def _browse_cue(self, combo: QComboBox) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "Choose a local WAV cue",
            "",
            "WAV audio (*.wav)",
        )
        if path:
            combo.setEditText(path)

    def _test_cue(self, combo: QComboBox, default_name: str) -> None:
        if not play_cue(
            combo.currentText(),
            default_name=default_name,
            output_index=self.output_combo.currentData(),
            delay_ms=int(self.bluetooth_delay_combo.currentData()),
        ):
            QMessageBox.warning(self, "Sound cue", "That local WAV cue could not be played.")

    def _collect_model_status(self, speech_model: str) -> None:
        speech = self.engine.transcriber.status()
        speech["model"] = speech_model
        speech["path"] = str(local_model_path(speech_model) or "")
        speech["available"] = bool(speech["path"])
        speech_text = (
            f"Speech AI ready on {str(speech['device']).upper()}."
            if speech["available"]
            else "Speech AI is unavailable. Repair or reinstall Manuscript."
        )
        writing_text = (
            "Writing AI is managed by the automatic hardware plan."
            if self.settings.polish_model_pack_id
            else "Writing AI will be selected automatically after the hardware check."
        )
        try:
            self.model_status_ready.emit(f"{speech_text}\n{writing_text}")
        except RuntimeError:
            pass

    def _sync_model_language(self) -> None:
        model_name = str(self.model_combo.currentData() or "")
        english_only = model_name.casefold().endswith(".en")
        self.language_combo.setEnabled(not english_only)
        if english_only:
            _select_data(self.language_combo, "en")
            self.language_combo.setToolTip("The installed speech AI supports English only.")
        else:
            self.language_combo.setToolTip("Choose a language or use automatic detection.")
        if hasattr(self, "remove_model_button"):
            current = next((item for item in self.installed_models if item.name == model_name), None)
            user_root = user_model_root().resolve(strict=False)
            is_user_pack = bool(
                current and current.path.resolve(strict=False).parent == user_root
            )
            self.remove_model_button.setEnabled(
                is_user_pack and model_name != self.settings.model_name
            )

    def _open_model_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(user_model_root())))

    def _start_model_import(self) -> None:
        source = QFileDialog.getExistingDirectory(
            self,
            "Choose a local speech AI pack",
        )
        if not source:
            return
        self.import_model_button.setEnabled(False)
        self.remove_model_button.setEnabled(False)
        self.model_status.setText("Validating and copying the local AI pack...")

        def work() -> None:
            try:
                pack = import_model_pack(source, user_model_root=user_model_root())
                payload = ("imported", pack.name, "Imported the speech AI locally.")
            except (OSError, ModelPackError) as exc:
                payload = ("error", "", f"AI import failed: {exc}")
            self.model_pack_ready.emit(payload)

        threading.Thread(target=work, daemon=True, name="echo-link-model-import").start()

    def _start_model_removal(self) -> None:
        name = str(self.model_combo.currentData() or "")
        if not name or name == self.settings.model_name:
            self.model_status.setText("Switch away from the active AI and save before removing it.")
            return
        answer = QMessageBox.question(
            self,
            "Remove local AI pack",
            "Remove the selected user-installed AI pack from this computer?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.import_model_button.setEnabled(False)
        self.remove_model_button.setEnabled(False)
        self.model_status.setText("Removing the selected AI pack...")

        def work() -> None:
            try:
                remove_model_pack(name, user_model_root=user_model_root())
                payload = ("removed", name, "Removed the selected AI pack.")
            except (OSError, ModelPackError) as exc:
                payload = ("error", "", f"AI removal failed: {exc}")
            self.model_pack_ready.emit(payload)

        threading.Thread(target=work, daemon=True, name="echo-link-model-remove").start()

    def _handle_model_pack_result(self, payload) -> None:
        action, name, message = payload
        selected = name if action == "imported" else self.settings.model_name
        self._refresh_model_choices(selected)
        self.import_model_button.setEnabled(True)
        self.model_status.setText(message)

    def _refresh_model_choices(self, selected: str) -> None:
        self.installed_models = discover_local_models()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for model in self.installed_models:
            label = "Multilingual speech AI" if model.multilingual else "English speech AI"
            self.model_combo.addItem(label, model.name)
        if self.model_combo.findData(selected) < 0:
            self.model_combo.addItem("Speech AI unavailable", selected)
        _select_data(self.model_combo, selected)
        self.model_combo.blockSignals(False)
        self._sync_model_language()
        self._refresh_recommendation()

    def _set_model_status(self, text: str) -> None:
        update_status_label(self.model_status, text, assertive="missing" in text.casefold())
        self.check_button.setEnabled(True)

    def _import_legacy_dictation(self) -> None:
        try:
            result = self.store.import_legacy_dictation()
        except Exception as exc:
            QMessageBox.warning(self, "Import failed", str(exc))
            return
        QMessageBox.information(
            self,
            "Import complete",
            f"Imported {result['settings']} settings and {result['vocabulary']} vocabulary entries.",
        )
        self.settings = self.store.get_settings()
        self.hotkey_edit.setText(self.settings.hotkey)

    def _save(self) -> None:
        try:
            hotkey = parse_hotkey(self.hotkey_edit.text()).canonical
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid hotkey", str(exc))
            self.hotkey_edit.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        try:
            hands_free_hotkey = parse_hotkey(self.hands_free_hotkey_edit.text()).canonical
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid hands-free hotkey", str(exc))
            self.hands_free_hotkey_edit.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        try:
            command_hotkey = parse_hotkey(self.command_hotkey_edit.text()).canonical
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid selected-text hotkey", str(exc))
            self.command_hotkey_edit.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        try:
            private_paste_hotkey = parse_hotkey(self.private_paste_hotkey_edit.text()).canonical
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid private paste hotkey", str(exc))
            self.private_paste_hotkey_edit.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        if hotkeys_overlap(hotkey, hands_free_hotkey):
            QMessageBox.warning(
                self,
                "Hotkeys overlap",
                "Push-to-talk and hands-free hotkeys must use different key combinations.",
            )
            self.hands_free_hotkey_edit.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        if hotkeys_overlap(hotkey, command_hotkey) or hotkeys_overlap(hands_free_hotkey, command_hotkey):
            QMessageBox.warning(
                self,
                "Hotkeys overlap",
                "Selected-text, push-to-talk, and hands-free hotkeys must be different.",
            )
            self.command_hotkey_edit.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        if any(
            hotkeys_overlap(private_paste_hotkey, other)
            for other in (hotkey, hands_free_hotkey, command_hotkey)
        ):
            QMessageBox.warning(
                self,
                "Hotkeys overlap",
                "Manuscript private paste must use a different shortcut.",
            )
            self.private_paste_hotkey_edit.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        self.settings = DictationSettings(
            hotkey=hotkey,
            microphone_index=self.microphone_combo.currentData(),
            # Store the name too. The index alone goes stale as soon as a
            # headset connects or disconnects, which is what left dictation
            # reporting no microphone with a working array attached.
            microphone_name=(
                ""
                if self.microphone_combo.currentData() is None
                else self.microphone_combo.currentText()
            ),
            mode=DictationMode(self.mode_combo.currentData()),
            model_name=str(self.model_combo.currentData() or "base.en"),
            device=str(self.device_combo.currentData()),
            compute_type="float16" if self.device_combo.currentData() == "cuda" else "int8",
            language=str(self.language_combo.currentData()),
            insert_text=self.insert_check.isChecked(),
            insertion_mode=InsertionMode(self.insertion_mode_combo.currentData()),
            keep_history=self.history_check.isChecked(),
            history_retention_days=self.history_retention_spin.value(),
            speech_logging_enabled=self.speech_logging_check.isChecked(),
            speech_log_retention_days=self.speech_retention_spin.value(),
            keep_recovery_text=self.recovery_check.isChecked(),
            personal_learning_enabled=self.learning_check.isChecked(),
            review_before_insert=self.review_check.isChecked(),
            streaming_enabled=self.streaming_check.isChecked(),
            streaming_interval_seconds=self.settings.streaming_interval_seconds,
            whisper_mode=self.whisper_check.isChecked(),
            append_trailing_space=self.trailing_check.isChecked(),
            local_refinement_enabled=self.refine_check.isChecked(),
            document_context_enabled=self.document_context_check.isChecked(),
            ollama_model=(
                self.settings.ollama_model if self.settings.polish_model_pack_id else ""
            ),
            polish_mode=PolishMode.coerce(self.polish_mode_combo.currentData()),
            polish_model_pack_id=self.settings.polish_model_pack_id,
            polish_policy_version=max(1, int(self.settings.polish_policy_version)),
            polish_download_consent=self.polish_download_consent_check.isChecked(),
            correction_timeout_seconds=self.settings.correction_timeout_seconds,
            gpu_idle_release_seconds=self.gpu_release_spin.value(),
            start_with_windows=self.startup_check.isChecked(),
            show_echo_bar=self.echo_bar_check.isChecked(),
            echo_bar_x=self.echo_bar_position[0],
            echo_bar_y=self.echo_bar_position[1],
            sound_enabled=self.sound_check.isChecked(),
            pause_media_during_dictation=self.pause_media_check.isChecked(),
            close_to_tray=self.close_to_tray_check.isChecked(),
            pre_roll_ms=self.pre_roll_spin.value(),
            max_recording_seconds=self.max_recording_spin.value(),
            start_sound=self.start_sound_combo.currentText().strip() or "Muted",
            stop_sound=self.stop_sound_combo.currentText().strip() or "Muted",
            audio_output_index=self.output_combo.currentData(),
            bluetooth_delay_ms=int(self.bluetooth_delay_combo.currentData()),
            hands_free_enabled=self.hands_free_check.isChecked(),
            hands_free_hotkey=hands_free_hotkey,
            command_hotkey=command_hotkey,
            automatic_writing_hud=self.automatic_writing_hud_check.isChecked(),
            private_paste_hotkey=private_paste_hotkey,
            silence_auto_stop_enabled=self.silence_stop_check.isChecked(),
            silence_timeout_seconds=self.silence_timeout_spin.value(),
        )
        self.accept()

    def _manage_learning(self) -> None:
        LearningManagerDialog(self.store, self).exec()

    def _delete_all_private_data(self) -> None:
        phrase, accepted = QInputDialog.getText(
            self,
            "Delete all private data",
            (
                "This deletes dictation history, encrypted speech logs, review feedback, learned corrections, snippets, "
                "recovery text, documents and revisions, evaluations, custom vocabulary, and "
                "private settings. The database is reset to safe defaults.\n\n"
                "This is a logical purge and SQLite compaction, not guaranteed secure erasure.\n\n"
                f"Type {self.PURGE_CONFIRMATION} to continue:"
            ),
            QLineEdit.EchoMode.Normal,
        )
        if not accepted:
            return
        if phrase.strip() != self.PURGE_CONFIRMATION:
            QMessageBox.warning(
                self,
                "Private data kept",
                "The confirmation text did not match. Nothing was deleted.",
            )
            return
        self.delete_private_data_button.setEnabled(False)
        try:
            result = self.store.purge_private_data(
                clear_product_log=_clear_product_log_files,
            )
        except Exception:
            self.delete_private_data_button.setEnabled(True)
            QMessageBox.critical(
                self,
                "Purge failed",
                "Manuscript could not complete the database purge. Private data may remain.",
            )
            return
        self.settings = self.store.get_settings()
        QMessageBox.information(
            self,
            "Private data deleted",
            (
                f"Deleted {result.deleted_row_count} stored rows. "
                f"Database: {result.database_status}. "
                f"WAL checkpoint: {result.wal_checkpoint_status}. "
                f"Vacuum: {result.vacuum_status}. "
                f"Product log: {result.product_log_status} "
                f"({result.product_log_files_cleared} files cleared).\n\n"
                "Settings and built-in vocabulary were reset to defaults. "
                "The active database encryption key was replaced. "
                "This does not claim secure erasure from SSDs, backups, snapshots, or copies."
            ),
        )
        self.accept()

    def _harden_data_folder(self) -> None:
        answer = QMessageBox.question(
            self,
            "Harden Windows data folder",
            (
                "Remove inherited broad permissions from Manuscript's local data folder?\n\n"
                "Your Windows account, Windows SYSTEM, and local administrators will keep full "
                "control. The database is encrypted separately. This adds account-level privacy "
                "but does not block administrators, backups, or malware running as you."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.harden_data_folder_button.setEnabled(False)
        try:
            result = harden_windows_data_folder(data_root())
        except DataFolderProtectionError:
            self.harden_data_folder_button.setEnabled(True)
            QMessageBox.critical(
                self,
                "Permissions unchanged or incomplete",
                "Windows could not finish hardening the Manuscript data folder.",
            )
            return
        QMessageBox.information(
            self,
            "Data folder hardened",
            (
                f"Verified {result.verified_targets} protected targets, including "
                f"{result.hardened_private_files} existing private files.\n\n"
                "This changes Windows permissions only. Database encryption remains separate. "
                "This does not claim secure erasure."
            ),
        )
        self.harden_data_folder_button.setEnabled(True)

    def _reset_echo_bar_position(self) -> None:
        self.echo_bar_position = (None, None)
        self.echo_bar_position_label.setText("Top center")


def _polish_pack_summary(store, pack_id: str) -> str:
    selected = str(pack_id or "").strip()
    if not selected:
        return (
            "AI is selected automatically for this computer. Size and measured "
            "speed appear after the local hardware check."
        )
    try:
        root = Path(store.path).parent / "polish-model-packs"
        packs = ModelPackManager(install_root=root).catalog(refresh_installed=False)
        pack = next(value for value in packs if value.pack_id == selected)
    except (OSError, RuntimeError, StopIteration, TypeError, ValueError):
        return "AI setup will be checked before use."
    size_gib = pack.size_bytes / (1024**3)
    runtime_note = ""
    try:
        snapshot = HardwareDetector().snapshot()
        backend = HardwarePolicyManager._select_backend(snapshot, pack)
        if backend is not RuntimeBackend.RULES:
            runtime_root = Path(store.path).parent / "polish-runtime-packs"
            runtimes = RuntimePackManager(install_root=runtime_root).candidates(
                backend,
                refresh_installed=False,
            )
            if runtimes:
                runtime_mib = runtimes[0].download_size_bytes / (1024**2)
                runtime_note = (
                    f" Signed {backend.value.upper()} support files: "
                    f"{runtime_mib:.0f} MB."
                )
    except (OSError, RuntimeError, TypeError, ValueError):
        runtime_note = ""
    return (
        f"Local AI download: {size_gib:.1f} GB. "
        f"Speed is measured locally after installation.{runtime_note}"
    )


def _select_data(combo: QComboBox, value) -> None:
    index = combo.findData(value)
    if index >= 0:
        combo.setCurrentIndex(index)


def _clear_product_log_files() -> int:
    """Clear only Manuscript's active log and three fixed rotation files."""

    base = log_path().resolve(strict=False)
    allowed = tuple(Path(f"{base}{suffix}") for suffix in ("", ".1", ".2", ".3"))
    root_logger = logging.getLogger()
    handler_settings = []
    for handler in tuple(root_logger.handlers):
        if not isinstance(handler, logging.FileHandler):
            continue
        try:
            handler_path = Path(handler.baseFilename).resolve(strict=False)
        except (OSError, RuntimeError, TypeError):
            continue
        if handler_path != base:
            continue
        handler_settings.append(
            (
                handler.level,
                handler.formatter,
                getattr(handler, "maxBytes", 1_000_000),
                getattr(handler, "backupCount", 3),
            )
        )
        root_logger.removeHandler(handler)
        handler.close()

    cleared = 0
    try:
        for candidate in allowed:
            if candidate.parent != base.parent or candidate.is_symlink() or not candidate.is_file():
                continue
            with candidate.open("w", encoding="utf-8", newline=""):
                pass
            cleared += 1
    finally:
        for level, formatter, max_bytes, backup_count in handler_settings:
            replacement = RotatingFileHandler(
                base,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            replacement.setLevel(level)
            replacement.setFormatter(formatter)
            root_logger.addHandler(replacement)
    return cleared
