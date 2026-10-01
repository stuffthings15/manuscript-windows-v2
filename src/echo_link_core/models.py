from __future__ import annotations

import re

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from .polish_types import PolishMode


OLLAMA_IDLE_UNLOAD_SECONDS = 5 * 60
OLLAMA_KEEP_ALIVE = f"{OLLAMA_IDLE_UNLOAD_SECONDS}s"

# What a local model may be called. Held here because two unrelated modules
# validate it - the pack installer and the drafting path - and two copies of a
# validation rule diverge the moment one of them is relaxed.
SAFE_MODEL_TAG = re.compile(
    r"[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)*(?::[a-z0-9][a-z0-9._-]*)?",
    re.IGNORECASE,
)


class DictationMode(str, Enum):
    FAST = "fast"
    DEVELOPER = "developer"
    REFINE = "refine"
    ACCURACY = "accuracy"


class InsertionMode(str, Enum):
    AUTO = "auto"
    CLIPBOARD = "clipboard"
    DIRECT = "direct"


class SessionState(str, Enum):
    READY = "ready"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    REFINING = "refining"
    INSERTED = "inserted"
    COMPLETE = "complete"
    ERROR = "error"


@dataclass(slots=True)
class DictationSettings:
    hotkey: str = "ctrl+win"
    microphone_index: int | None = None
    # The device name decides which microphone is opened; the index above is
    # only a hint, because sounddevice renumbers devices as they come and go.
    microphone_name: str = ""
    mode: DictationMode = DictationMode.FAST
    model_name: str = "base.en"
    device: str = "cpu"
    compute_type: str = "int8"
    language: str = "en"
    insert_text: bool = True
    insertion_mode: InsertionMode = InsertionMode.DIRECT
    keep_history: bool = True
    history_retention_days: int = 3
    speech_logging_enabled: bool = False
    speech_log_retention_days: int = 15
    keep_recovery_text: bool = False
    personal_learning_enabled: bool = False
    review_before_insert: bool = False
    streaming_enabled: bool = True
    streaming_interval_seconds: float = 2.0
    whisper_mode: bool = False
    append_trailing_space: bool = True
    local_refinement_enabled: bool = True
    # Let a dictated name be checked against the document it is being typed
    # into. Without this the corrector sees one utterance and nothing else, so
    # a cited author's name it has never met reads as a misspelling and gets
    # "corrected" into something the writer did not say. The document is only
    # evidence about which capitalised words are real names; no document text
    # is ever sent to the model.
    document_context_enabled: bool = True
    # Empty means the hardware policy must choose an accepted local AI pack.
    ollama_model: str = ""
    polish_mode: PolishMode = PolishMode.AUTO
    polish_model_pack_id: str = ""
    polish_policy_version: int = 1
    polish_download_consent: bool = False
    # Keep the writer's meaning exactly. OFF lets the local AI rewrite
    # for sense: restructure, drop filler, repair muddled claims. Asked
    # for on 2026-09-30. Default stays ON because OFF means the model may
    # change what the writer said, and that is the writer's call to make.
    polish_faithful: bool = True
    correction_timeout_seconds: float = 20.0
    gpu_idle_release_seconds: int = OLLAMA_IDLE_UNLOAD_SECONDS
    start_with_windows: bool = False
    show_echo_bar: bool = True
    echo_bar_x: int | None = None
    echo_bar_y: int | None = None
    # Where the writing badge sits RELATIVE TO THE TEXT it was captured from,
    # not on the screen: x from the anchor's right edge, y from its top. A
    # screen position would be wrong the moment the writer scrolled or moved
    # the window. None means the default placement, above and to the right.
    # Set automatically whenever the badge is dragged, so moving it once is
    # the whole of the preference.
    hud_badge_offset_x: int | None = None
    hud_badge_offset_y: int | None = None
    # The default corner used until the badge is dragged.
    hud_badge_position: str = "above-right"
    sound_enabled: bool = True
    pause_media_during_dictation: bool = True
    close_to_tray: bool = True
    pre_roll_ms: int = 500
    max_recording_seconds: int = 120
    start_sound: str = "Speech On.wav"
    stop_sound: str = "Built-in completion tone"
    audio_output_index: int | None = None
    bluetooth_delay_ms: int = -1
    hands_free_enabled: bool = True
    hands_free_hotkey: str = "ctrl+alt+space"
    command_hotkey: str = "ctrl+shift+space"
    automatic_writing_hud: bool = True
    private_paste_hotkey: str = "ctrl+alt+v"
    silence_auto_stop_enabled: bool = True
    silence_timeout_seconds: float = 2.5

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["mode"] = self.mode.value
        result["insertion_mode"] = self.insertion_mode.value
        result["polish_mode"] = self.polish_mode.value
        return result

    @classmethod
    def from_dict(cls, values: dict[str, Any] | None) -> "DictationSettings":
        values = dict(values or {})
        if "history_retention_days" not in values:
            # Existing installs predate retention controls. Move them to the
            # new privacy-bounded default once; later explicit choices persist.
            values["keep_history"] = True
            values["history_retention_days"] = 3
        try:
            values["history_retention_days"] = max(
                1,
                min(365, int(values.get("history_retention_days", 3))),
            )
        except (TypeError, ValueError):
            values["history_retention_days"] = 3
        values["gpu_idle_release_seconds"] = OLLAMA_IDLE_UNLOAD_SECONDS
        if str(values.get("ollama_model") or "").casefold() in {
            "qwen2.5:1.5b",
            "qwen2.5:3b",
            "qwen2.5:7b",
            "qwen2.5:14b",
            "qwen3:4b",
            "qwen3:8b",
            "qwen3:14b",
        }:
            values["ollama_model"] = ""
        if str(values.get("polish_model_pack_id") or "").casefold() in {
            "qwen3-1.7b-q4-k-m-v1",
            "qwen3-4b-q4-k-m-v1",
            "qwen3-8b-q4-k-m-v1",
            "qwen3-14b-q4-k-m-v1",
            "qwen3-14b-q5-v1",
            "qwen3-14b-q6-v1",
        }:
            values["polish_model_pack_id"] = ""
        if str(values.get("start_sound") or "").casefold() == "built-in activation tone":
            values["start_sound"] = "Speech On.wav"
        try:
            values["mode"] = DictationMode(values.get("mode", DictationMode.FAST.value))
        except ValueError:
            values["mode"] = DictationMode.FAST
        try:
            values["insertion_mode"] = InsertionMode(
                values.get("insertion_mode", InsertionMode.AUTO.value)
            )
        except ValueError:
            values["insertion_mode"] = InsertionMode.AUTO
        values["polish_mode"] = PolishMode.coerce(
            values.get("polish_mode", PolishMode.AUTO.value)
        )
        try:
            values["polish_policy_version"] = max(
                1,
                int(values.get("polish_policy_version", 1)),
            )
        except (TypeError, ValueError):
            values["polish_policy_version"] = 1
        values["polish_download_consent"] = bool(
            values.get("polish_download_consent", False)
        )
        values["polish_faithful"] = bool(values.get("polish_faithful", True))
        allowed = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in values.items() if key in allowed})


@dataclass(slots=True)
class SessionEvent:
    state: SessionState
    message: str
    raw_text: str = ""
    final_text: str = ""
    latency_ms: int | None = None
    inserted: bool | None = None
    warning: str = ""
    review_pending: bool = False
    is_partial: bool = False
    stable_text: str = ""
    partial_insertion: bool = False
    insertion_uncertain: bool = False
    possibly_inserted_utf16_units: int = 0
    total_insertion_utf16_units: int = 0
    capture_id: int = 0
    job_sequence: int = 0
    capture_started: bool = False
    capture_stopped: bool = False
