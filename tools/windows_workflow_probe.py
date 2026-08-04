from __future__ import annotations

import argparse
import ctypes
import gc
import hashlib
import json
import math
import os
import socket
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, TypeVar

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from echo_link_core.data_protection import validate_local_export_destination

KEYEVENTF_KEYUP = 0x0002
SW_RESTORE = 9
FAST_TAP_MAX_MS = 100.0
FAST_TAP_PRESS_DELAY_SECONDS = 0.5
FAST_TAP_SETTLE_TIMEOUT_SECONDS = 6.0
DEFAULT_PROBE_HOTKEY = "ctrl+alt+f12"
SYNTHETIC_PHRASE = "Echo Link packaged workflow test"
SOURCE_REQUIRED_CHECKS = (
    "microphone_stream_ready",
    "synthetic_input_ready",
    "separate_process_target_ready",
    "target_focused_before_hotkey",
    "hotkey_registered",
    "hotkey_press_started_listening",
    "hud_visible_while_listening",
    "hud_reports_listening",
    "hud_did_not_steal_focus",
    "hud_rejects_focus",
    "hotkey_release_completed_pipeline",
    "activation_cue_played",
    "stop_cue_played",
    "local_model_loaded",
    "local_transcription_produced_text",
    "cross_process_text_inserted",
    "clipboard_restored",
    "direct_unicode_newline_inserted",
    "direct_mode_left_clipboard_untouched",
    "direct_supplementary_rejected_safely",
    "fast_tap_is_short",
    "fast_tap_press_before_release",
    "fast_tap_callbacks_serialized",
    "fast_tap_session_ordered",
    "fast_tap_not_stuck",
    "fast_tap_insertion_disabled",
    "no_recorded_audio_persisted",
)
PACKAGED_REQUIRED_CHECKS = (
    "isolated_data_dir",
    "network_disabled",
    "live_microphone_not_opened",
    "synthetic_input_ready",
    "separate_process_target_ready",
    "target_focused_before_hotkey",
    "hotkey_registered",
    "hotkey_press_started_listening",
    "hud_visible_while_listening",
    "hud_reports_listening",
    "hud_did_not_steal_focus",
    "hud_rejects_focus",
    "hotkey_release_completed_pipeline",
    "activation_cue_played",
    "stop_cue_played",
    "local_model_loaded",
    "local_transcription_produced_text",
    "cross_process_text_inserted",
    "direct_mode_left_clipboard_untouched",
    "no_recorded_audio_persisted",
)
_T = TypeVar("_T")


class ProbeNetworkDisabledError(RuntimeError):
    pass


class _PackagedExactWorkflowComplete(Exception):
    pass


class SyntheticSpeechRecorder:
    """Feed private, generated speech through the normal engine boundary."""

    def __init__(self, audio: np.ndarray):
        self.audio = np.ascontiguousarray(audio, dtype=np.int16)
        self.started = False
        self.level_callback = None

    def start(self, _device_index=None, level_callback=None) -> None:
        self.started = True
        self.level_callback = level_callback
        if level_callback is not None:
            level_callback(0.35)

    def stop(self) -> np.ndarray:
        self.started = False
        return self.audio.copy()

    def snapshot(self) -> np.ndarray:
        return self.audio.copy() if self.started else np.zeros(0, dtype=np.int16)

    def arm(self, _device_index=None, _pre_roll_ms=0) -> None:
        return None

    def cancel(self) -> None:
        self.started = False
        self.level_callback = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json_atomic(path: Path, payload: dict) -> None:
    path = validate_local_export_destination(
        path,
        allowed_suffixes={".json"},
        overwrite=True,
        create_parent=True,
    )
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


@contextmanager
def _network_disabled() -> Iterator[None]:
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def deny_connect(_socket, _address):
        raise ProbeNetworkDisabledError("Network access is disabled during the workflow probe.")

    def deny_connect_ex(_socket, _address):
        raise ProbeNetworkDisabledError("Network access is disabled during the workflow probe.")

    socket.socket.connect = deny_connect
    socket.socket.connect_ex = deny_connect_ex
    try:
        yield
    finally:
        socket.socket.connect = original_connect
        socket.socket.connect_ex = original_connect_ex


def _run_in_isolated_environment(callback: Callable[[Path], _T]) -> _T:
    managed_names = (
        "ECHOLINK_DATA_DIR",
        "ECHOLINK_DISABLE_MEDIA_CONTROL",
        "ECHOLINK_DISABLE_TRAY",
        "HF_HUB_OFFLINE",
        "TRANSFORMERS_OFFLINE",
        "QT_QPA_PLATFORM",
    )
    previous = {name: os.environ.get(name) for name in managed_names}
    with tempfile.TemporaryDirectory(
        prefix="echo-link-packaged-probe-",
        ignore_cleanup_errors=True,
    ) as raw_root:
        root = Path(raw_root)
        isolated_data = root / "data"
        isolated_data.mkdir()
        os.environ["ECHOLINK_DATA_DIR"] = str(isolated_data)
        os.environ["ECHOLINK_DISABLE_MEDIA_CONTROL"] = "1"
        os.environ["ECHOLINK_DISABLE_TRAY"] = "1"
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ.pop("QT_QPA_PLATFORM", None)
        try:
            with _network_disabled():
                return callback(root)
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def _target_command(title: str, status_path: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        return [
            sys.executable,
            "--workflow-probe-target",
            "--probe-target-title",
            title,
            "--probe-target-status",
            str(status_path),
        ]
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "--target-mode",
        "--target-title",
        title,
        "--target-status",
        str(status_path),
    ]


def _target_working_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return ROOT


def _run_target(title: str, status_path: Path) -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QPlainTextEdit

    app = QApplication([])
    target = QPlainTextEdit()
    target.setWindowTitle(title)
    target.setPlaceholderText("Manuscript probe target")
    target.resize(620, 88)
    target.show()
    target.raise_()
    target.activateWindow()
    target.setFocus()
    focus_until = time.monotonic() + 12.0

    def publish() -> None:
        try:
            if time.monotonic() < focus_until and not target.toPlainText():
                target.raise_()
                target.activateWindow()
                target.setFocus()
            _write_json_atomic(
                status_path,
                {
                    "pid": os.getpid(),
                    "window_handle": int(target.winId()),
                    "text": target.toPlainText(),
                },
            )
        finally:
            QTimer.singleShot(40, publish)

    QTimer.singleShot(0, publish)
    QTimer.singleShot(120, target.activateWindow)
    QTimer.singleShot(140, target.setFocus)
    return int(app.exec())


def _read_target_status(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _wait_for_target(path: Path, process: subprocess.Popen, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return {}
        status = _read_target_status(path)
        if int(status.get("window_handle") or 0) and int(status.get("pid") or 0):
            return status
        time.sleep(0.04)
    return {}


def _focus_window(handle: int, app, timeout: float = 2.0) -> bool:
    user32 = ctypes.windll.user32
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        user32.ShowWindow(handle, SW_RESTORE)
        user32.keybd_event(0x12, 0, 0, 0)
        user32.SetForegroundWindow(handle)
        user32.BringWindowToTop(handle)
        user32.keybd_event(0x12, 0, KEYEVENTF_KEYUP, 0)
        app.processEvents()
        if int(user32.GetForegroundWindow() or 0) == int(handle):
            return True
        time.sleep(0.05)
    return False


def _set_keys(keys: list[int], *, up: bool) -> None:
    flags = KEYEVENTF_KEYUP if up else 0
    for key in keys:
        ctypes.windll.user32.keybd_event(int(key), 0, flags, 0)
        time.sleep(0.015)


def _release_keys(keys: list[int]) -> None:
    _set_keys(list(reversed(keys)), up=True)


def _tap_keys_fast(keys: list[int]) -> float:
    started = time.monotonic()
    pressed: list[int] = []
    try:
        for key in keys:
            ctypes.windll.user32.keybd_event(int(key), 0, 0, 0)
            pressed.append(key)
            time.sleep(0.003)
    finally:
        for key in reversed(pressed):
            ctypes.windll.user32.keybd_event(int(key), 0, KEYEVENTF_KEYUP, 0)
            time.sleep(0.003)
    return (time.monotonic() - started) * 1000.0


def _pump_until(app, predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return bool(predicate())


def _generate_tone(path: Path, frequency: float) -> None:
    sample_rate = 16_000
    duration_seconds = 0.08
    frame_count = int(sample_rate * duration_seconds)
    samples = np.array(
        [
            int(32767 * 0.035 * math.sin(2.0 * math.pi * frequency * index / sample_rate))
            for index in range(frame_count)
        ],
        dtype="<i2",
    )
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(samples.tobytes())


def _generate_speech(path: Path, phrase: str) -> dict:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        return {"passed": False, "error": "Windows PowerShell was not found."}
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, "
        "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, "
        "[System.Speech.AudioFormat.AudioChannel]::Mono); "
        "try { $s.SetOutputToWaveFile($env:ECHOLINK_PROBE_WAV, $f); "
        "$s.Speak($env:ECHOLINK_PROBE_PHRASE) } finally { $s.Dispose() }"
    )
    environment = os.environ.copy()
    environment["ECHOLINK_PROBE_WAV"] = str(path)
    environment["ECHOLINK_PROBE_PHRASE"] = phrase
    completed = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=30,
        env=environment,
        check=False,
    )
    if completed.returncode != 0 or not path.is_file():
        error = (completed.stderr or completed.stdout or "Speech synthesis failed.").strip()
        return {"passed": False, "error": error}
    try:
        with wave.open(str(path), "rb") as source:
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            sample_rate = source.getframerate()
            frames = source.readframes(source.getnframes())
    except (OSError, wave.Error) as exc:
        return {"passed": False, "error": str(exc)}
    if channels != 1 or sample_width != 2 or sample_rate != 16_000:
        return {
            "passed": False,
            "error": f"Unexpected TTS format: {channels} channel(s), {sample_width * 8}-bit, {sample_rate} Hz.",
        }
    audio = np.frombuffer(frames, dtype="<i2").copy()
    return {
        "passed": audio.size >= 4000,
        "audio": audio,
        "sample_count": int(audio.size),
        "sample_rate": sample_rate,
        "source": "Windows System.Speech synthetic voice",
    }


def _probe_microphone(duration_seconds: float = 0.35) -> dict:
    from echo_link_core.audio import MicrophoneRecorder, list_microphones

    recorder = MicrophoneRecorder()
    levels: list[float] = []
    started = time.monotonic()
    try:
        recorder.start(level_callback=levels.append)
        time.sleep(max(0.2, duration_seconds))
        audio = recorder.stop()
        elapsed = time.monotonic() - started
        peak = int(np.max(np.abs(audio.astype(np.int32)))) if audio.size else 0
        return {
            "passed": bool(audio.size),
            "sample_count": int(audio.size),
            "duration_seconds": round(elapsed, 3),
            "peak_level": peak,
            "level_callbacks": len(levels),
            "available_input_count": len(list_microphones()),
            "retained_audio": False,
        }
    except Exception as exc:
        return {"passed": False, "error": str(exc), "retained_audio": False}
    finally:
        recorder.cancel()


def _git_state() -> dict:
    if getattr(sys, "frozen", False):
        from echo_link_core.paths import resource_path

        commit = ""
        try:
            payload = json.loads(
                resource_path("resources", "build_info.json").read_text(encoding="utf-8")
            )
            commit = str(payload.get("commit") or "")
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass
        executable = Path(sys.executable).resolve()
        try:
            executable_hash = hashlib.sha256(executable.read_bytes()).hexdigest()
        except OSError:
            executable_hash = ""
        return {
            "runtime": "packaged",
            "head_commit": commit,
            "worktree_clean_at_start": None,
            "changed_files_at_start": [],
            "harness_sha256": executable_hash,
            "source_fingerprint_sha256": executable_hash,
        }

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        timeout=5,
        check=False,
    )
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=ROOT,
        capture_output=True,
        timeout=5,
        check=False,
    )
    diff = subprocess.run(
        ["git", "diff", "--binary", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        timeout=10,
        check=False,
    )
    head_text = head.stdout.decode("utf-8", errors="replace").strip() if head.returncode == 0 else ""
    status_text = status.stdout.decode("utf-8", errors="replace").strip()
    harness_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    fingerprint = hashlib.sha256(
        b"\n".join((head.stdout.strip(), status.stdout, diff.stdout, harness_hash.encode("ascii")))
    ).hexdigest()
    return {
        "head_commit": head_text,
        "worktree_clean_at_start": not bool(status_text),
        "changed_files_at_start": status_text.splitlines(),
        "harness_sha256": harness_hash,
        "source_fingerprint_sha256": fingerprint,
    }


def run_probe(args) -> dict:
    if sys.platform != "win32":
        return {
            "schema_version": 3,
            "probe": "windows_dictation_workflow",
            "passed": False,
            "error": "This acceptance probe requires Windows.",
        }

    if getattr(args, "packaged_mode", False):
        return _run_in_isolated_environment(
            lambda isolated_root: _run_probe_impl(args, isolated_root, packaged_mode=True)
        )
    return _run_probe_impl(args, None, packaged_mode=False)


def _run_probe_impl(
    args,
    isolated_root: Path | None,
    *,
    packaged_mode: bool,
) -> dict:
    if not packaged_mode:
        os.environ.pop("QT_QPA_PLATFORM", None)
        os.environ["ECHOLINK_DISABLE_MEDIA_CONTROL"] = "1"
        os.environ["ECHOLINK_DISABLE_TRAY"] = "1"

    import pyperclip
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    import echo_link_app.sound_cues as sound_cues
    from echo_link_app.window import EchoLinkWindow
    from echo_link_core.engine import DictationEngine
    from echo_link_core.hotkeys import GlobalPushToTalk, parse_hotkey
    from echo_link_core.insertion import WindowsTextInserter, active_window
    from echo_link_core.models import DictationMode, InsertionMode, SessionState
    from echo_link_core.storage import EchoLinkStore
    from echo_link_core.transcription import LocalWhisperTranscriber

    source_state = _git_state()
    synthetic_phrase = SYNTHETIC_PHRASE if packaged_mode else args.phrase
    evidence = {
        "schema_version": 4 if packaged_mode else 3,
        "probe": (
            "packaged_windows_dictation_workflow"
            if packaged_mode
            else "windows_dictation_workflow"
        ),
        "started_at": _utc_now(),
        "code_commit": source_state["head_commit"],
        "source_state": source_state,
        "synthetic_phrase": synthetic_phrase,
        "checks": {},
        "details": {},
        "limitations": (
            [
                "The packaged probe never opens the live microphone and never uses human speech.",
                "Synthetic Windows speech is fed through the normal engine/transcriber/correction/insertion path.",
                "This probe does not replace physical spoken-word, Bluetooth, Word, chat, terminal, or elevated-window acceptance.",
            ]
            if packaged_mode
            else [
                "The live microphone stream and local speech recognition are checked separately so no human speech is required.",
                "Synthetic Windows speech is fed through the normal engine/transcriber/correction/insertion path.",
                "This probe does not replace physical spoken-word, Bluetooth, Word, chat, terminal, or elevated-window acceptance.",
            ]
        ),
    }
    checks = evidence["checks"]
    details = evidence["details"]
    if packaged_mode:
        isolated_data = (isolated_root / "data").resolve()
        checks["isolated_data_dir"] = bool(
            Path(os.environ.get("ECHOLINK_DATA_DIR", "")).resolve() == isolated_data
        )
        checks["network_disabled"] = True
        checks["live_microphone_not_opened"] = True
        details["isolation"] = {
            "temporary_data_directory": True,
            "environment_restored_after_probe": True,
            "network_sockets_disabled": True,
            "live_microphone_opened": False,
            "synthetic_text_only": True,
        }
        microphone = {
            "passed": False,
            "skipped": True,
            "reason": "Live microphone access is forbidden in packaged probe mode.",
            "retained_audio": False,
        }
    elif args.skip_microphone:
        microphone = {"passed": False, "skipped": True, "retained_audio": False}
    else:
        microphone = _probe_microphone()
    details["microphone"] = microphone
    if not packaged_mode:
        checks["microphone_stream_ready"] = bool(microphone.get("passed"))

    app = None
    target_process = None
    target_handle = 0
    listener = None
    window = None
    transcriber = None
    keys: list[int] = []
    cue_calls: list[dict] = []
    cue_lock = threading.Lock()
    original_play_blocking = sound_cues._play_blocking
    temporary_path: Path | None = None
    try:
        original_clipboard = pyperclip.paste()
        clipboard_was_available = True
    except Exception as exc:
        original_clipboard = ""
        clipboard_was_available = False
        details["clipboard_error"] = str(exc)

    try:
        with tempfile.TemporaryDirectory(
            prefix="echo-link-workflow-",
            ignore_cleanup_errors=True,
        ) as raw_temp:
            temporary = Path(raw_temp)
            temporary_path = temporary
            status_path = temporary / "target.json"
            speech_path = temporary / "synthetic-speech.wav"
            start_cue = temporary / "start-cue.wav"
            stop_cue = temporary / "stop-cue.wav"
            _generate_tone(start_cue, 660.0)
            _generate_tone(stop_cue, 440.0)

            speech = _generate_speech(speech_path, synthetic_phrase)
            audio = speech.pop("audio", np.zeros(0, dtype=np.int16))
            details["synthetic_input"] = speech
            checks["synthetic_input_ready"] = bool(speech.get("passed"))
            if not checks["synthetic_input_ready"]:
                raise RuntimeError(str(speech.get("error") or "Synthetic speech was not created."))

            target_title = f"Manuscript workflow target {os.getpid()}-{time.time_ns()}"
            child_environment = os.environ.copy()
            child_environment.pop("QT_QPA_PLATFORM", None)
            target_process = subprocess.Popen(
                _target_command(target_title, status_path),
                cwd=_target_working_directory(),
                env=child_environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            target_status = _wait_for_target(status_path, target_process, timeout=15.0)
            target_handle = int(target_status.get("window_handle") or 0)
            target_pid = int(target_status.get("pid") or 0)
            checks["separate_process_target_ready"] = bool(
                target_handle and target_pid and target_pid != os.getpid()
            )
            details["target"] = {
                "pid": target_pid,
                "launcher_pid": target_process.pid,
                "window_handle": target_handle,
                "separate_process": target_pid != os.getpid(),
            }
            if not checks["separate_process_target_ready"]:
                error = ""
                if target_process.poll() is not None and target_process.stderr is not None:
                    error = target_process.stderr.read().strip()
                raise RuntimeError(error or "The separate-process target window did not start.")

            app = QApplication.instance() or QApplication([])

            store = (
                EchoLinkStore()
                if packaged_mode
                else EchoLinkStore(temporary / "workflow.db")
            )
            settings = store.get_settings()
            settings.hotkey = args.hotkey
            settings.mode = DictationMode.FAST
            settings.insert_text = True
            if packaged_mode:
                settings.insertion_mode = InsertionMode.DIRECT
            settings.review_before_insert = False
            settings.streaming_enabled = False
            if packaged_mode:
                settings.local_refinement_enabled = False
            settings.keep_history = False
            settings.keep_recovery_text = False
            settings.personal_learning_enabled = False
            settings.show_echo_bar = True
            settings.sound_enabled = True
            settings.pause_media_during_dictation = False
            settings.pre_roll_ms = 0
            settings.start_sound = str(start_cue)
            settings.stop_sound = str(stop_cue)
            settings.audio_output_index = None
            settings.bluetooth_delay_ms = 0
            store.save_settings(settings)

            window = EchoLinkWindow(store, enable_hotkey=False, enable_tray=False)
            window.engine.shutdown()
            session_events = []

            def on_session(event) -> None:
                session_events.append(event)
                window.bridge.session_event.emit(event)

            transcriber = LocalWhisperTranscriber(settings)
            engine = DictationEngine(
                store,
                recorder=SyntheticSpeechRecorder(audio),
                transcriber=transcriber,
                inserter=WindowsTextInserter(settings.insertion_mode),
                event_callback=on_session,
                level_callback=window.bridge.level_changed.emit,
            )
            window.engine = engine
            window._last_session_state = SessionState.READY

            def witnessed_play(path, output_index, delay_ms):
                started = time.monotonic()
                result = original_play_blocking(path, output_index, delay_ms)
                with cue_lock:
                    cue_calls.append(
                        {
                            "file": Path(path).name,
                            "played": bool(result),
                            "delay_ms": int(delay_ms),
                            "elapsed_ms": round((time.monotonic() - started) * 1000),
                        }
                    )
                return result

            sound_cues._play_blocking = witnessed_play

            focused = _focus_window(target_handle, app)
            captured = active_window()
            checks["target_focused_before_hotkey"] = bool(
                focused and captured.handle == target_handle and captured.process_id == target_pid
            )
            if not checks["target_focused_before_hotkey"]:
                raise RuntimeError("The probe target could not take foreground focus.")

            hotkey_errors: list[str] = []
            listener = GlobalPushToTalk(
                settings.hotkey,
                window.bridge.hotkey_pressed.emit,
                window.bridge.hotkey_released.emit,
                hotkey_errors.append,
            )
            listener.start()
            checks["hotkey_registered"] = listener.wait_until_ready(timeout=2.0)
            details["hotkey"] = {
                "combination": settings.hotkey,
                "registration_error": listener.registration_error or (hotkey_errors[-1] if hotkey_errors else ""),
            }
            if not checks["hotkey_registered"]:
                raise RuntimeError(details["hotkey"]["registration_error"] or "The probe hotkey did not register.")

            keys = sorted(parse_hotkey(settings.hotkey).keys)
            _set_keys(keys, up=False)
            listening = _pump_until(app, lambda: engine.state is SessionState.LISTENING, 3.0)
            app.processEvents()
            checks["hotkey_press_started_listening"] = bool(listening)
            checks["hud_visible_while_listening"] = bool(window.echo_bar.isVisible())
            checks["hud_reports_listening"] = "listening" in window.echo_bar.status.text().casefold()
            checks["hud_did_not_steal_focus"] = int(ctypes.windll.user32.GetForegroundWindow() or 0) == target_handle
            checks["hud_rejects_focus"] = bool(
                window.echo_bar.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
            )
            details["hud"] = {
                "status": window.echo_bar.status.text(),
                "level_percent": window.echo_bar.level.value(),
                "window_handle": int(window.echo_bar.winId()),
            }
            if args.screenshot:
                screenshot = validate_local_export_destination(
                    args.screenshot,
                    allowed_suffixes={".png"},
                    overwrite=True,
                    create_parent=True,
                )
                checks["hud_screenshot_saved"] = bool(
                    window.echo_bar.grab().save(str(screenshot))
                )
                details["hud"]["screenshot"] = str(screenshot)

            _release_keys(keys)
            keys = []
            complete = _pump_until(
                app,
                lambda: engine.state in {SessionState.COMPLETE, SessionState.ERROR},
                args.timeout,
            )
            checks["hotkey_release_completed_pipeline"] = bool(
                complete and engine.state is SessionState.COMPLETE
            )

            _pump_until(app, lambda: len(cue_calls) >= 2, 3.0)
            with cue_lock:
                cue_snapshot = list(cue_calls)
            details["cues"] = cue_snapshot
            checks["activation_cue_played"] = bool(
                any(item["file"] == start_cue.name and item["played"] for item in cue_snapshot)
            )
            checks["stop_cue_played"] = bool(
                any(item["file"] == stop_cue.name and item["played"] for item in cue_snapshot)
            )

            final_event = next(
                (event for event in reversed(session_events) if event.state is SessionState.COMPLETE),
                None,
            )
            error_event = next(
                (event for event in reversed(session_events) if event.state is SessionState.ERROR),
                None,
            )
            final_text = final_event.final_text if final_event is not None else ""
            raw_text = final_event.raw_text if final_event is not None else ""
            details["pipeline"] = {
                "states": [event.state.value for event in session_events],
                "raw_text": raw_text,
                "final_text": final_text,
                "error": error_event.message if error_event is not None else "",
                "latency_ms": final_event.latency_ms if final_event is not None else None,
            }
            if packaged_mode:
                captured_target = engine._target_window
                current_target = active_window()
                details["target_capture"] = {
                    "captured_handle": int(captured_target.handle),
                    "captured_process_id": int(captured_target.process_id),
                    "expected_handle": target_handle,
                    "expected_process_id": target_pid,
                    "captured_expected_target": bool(
                        captured_target.handle == target_handle
                        and captured_target.process_id == target_pid
                    ),
                    "active_after_pipeline_handle": int(current_target.handle),
                    "active_after_pipeline_process_id": int(current_target.process_id),
                }
            model_status = transcriber.status()
            details["transcriber"] = model_status
            checks["local_model_loaded"] = bool(model_status.get("available") and model_status.get("loaded"))
            checks["local_transcription_produced_text"] = bool(raw_text.strip())

            target_text = ""
            target_deadline = time.monotonic() + 3.0
            while time.monotonic() < target_deadline:
                app.processEvents()
                target_text = str(_read_target_status(status_path).get("text") or "")
                if final_text and target_text == final_text:
                    break
                time.sleep(0.04)
            details["target"]["inserted_text"] = target_text
            checks["cross_process_text_inserted"] = bool(final_text and target_text == final_text)

            if packaged_mode and clipboard_was_available:
                try:
                    checks["direct_mode_left_clipboard_untouched"] = (
                        pyperclip.paste() == original_clipboard
                    )
                except Exception as exc:
                    checks["direct_mode_left_clipboard_untouched"] = False
                    details["clipboard_error"] = str(exc)
            elif clipboard_was_available:
                try:
                    _pump_until(app, lambda: pyperclip.paste() == original_clipboard, 2.0)
                    checks["clipboard_restored"] = pyperclip.paste() == original_clipboard
                except Exception as exc:
                    checks["clipboard_restored"] = False
                    details["clipboard_error"] = str(exc)
            else:
                checks["clipboard_restored"] = False

            if packaged_mode:
                checks["no_recorded_audio_persisted"] = not any(
                    path.name.startswith("record") and path.suffix.casefold() == ".wav"
                    for path in temporary.rglob("*.wav")
                )
                raise _PackagedExactWorkflowComplete

            direct_suffix = "\nDirect Unicode: café 世界"
            direct_clipboard_before = pyperclip.paste() if clipboard_was_available else None
            direct_record = WindowsTextInserter(InsertionMode.DIRECT).insert_into(
                captured,
                direct_suffix,
            )
            direct_expected = f"{final_text}{direct_suffix}"
            direct_text = ""
            direct_deadline = time.monotonic() + 3.0
            while time.monotonic() < direct_deadline:
                app.processEvents()
                direct_text = str(_read_target_status(status_path).get("text") or "")
                if direct_text == direct_expected:
                    break
                time.sleep(0.04)
            details["target"]["direct_unicode_suffix"] = direct_suffix
            details["target"]["direct_unicode_result"] = direct_text
            checks["direct_unicode_newline_inserted"] = bool(
                direct_record is not None and direct_text == direct_expected
            )
            checks["direct_mode_left_clipboard_untouched"] = bool(
                clipboard_was_available and pyperclip.paste() == direct_clipboard_before
            )
            target_before_emoji = direct_text
            emoji_error = ""
            try:
                WindowsTextInserter(InsertionMode.DIRECT).insert_into(captured, "😀")
            except RuntimeError as exc:
                emoji_error = str(exc)
            _pump_until(
                app,
                lambda: str(_read_target_status(status_path).get("text") or "")
                == target_before_emoji,
                0.5,
            )
            target_after_emoji = str(_read_target_status(status_path).get("text") or "")
            details["target"]["supplementary_rejection"] = emoji_error
            checks["direct_supplementary_rejected_safely"] = bool(
                "Use Auto or Clipboard" in emoji_error
                and target_after_emoji == target_before_emoji
            )

            listener.stop()
            listener.join(timeout=1.5)
            listener = None

            if not _focus_window(target_handle, app):
                raise RuntimeError("The probe target could not regain focus for the fast-tap check.")
            engine.settings.insert_text = False
            engine.settings.sound_enabled = False
            fast_initial_state = engine.state.value
            fast_event_start = len(session_events)
            fast_target_before = str(_read_target_status(status_path).get("text") or "")
            fast_trace: list[dict[str, object]] = []
            fast_trace_lock = threading.Lock()
            fast_started = time.monotonic()

            def delayed_fast_press() -> None:
                time.sleep(FAST_TAP_PRESS_DELAY_SECONDS)
                with fast_trace_lock:
                    fast_trace.append(
                        {
                            "action": "press",
                            "elapsed_ms": round((time.monotonic() - fast_started) * 1000.0, 3),
                            "thread_id": threading.get_ident(),
                        }
                    )
                window.bridge.hotkey_pressed.emit()

            def fast_release() -> None:
                with fast_trace_lock:
                    fast_trace.append(
                        {
                            "action": "release",
                            "elapsed_ms": round((time.monotonic() - fast_started) * 1000.0, 3),
                            "thread_id": threading.get_ident(),
                        }
                    )
                window.bridge.hotkey_released.emit()

            fast_errors: list[str] = []
            listener = GlobalPushToTalk(
                settings.hotkey,
                delayed_fast_press,
                fast_release,
                fast_errors.append,
            )
            listener.start()
            fast_registered = listener.wait_until_ready(timeout=2.0)
            if not fast_registered:
                raise RuntimeError(
                    listener.registration_error
                    or (fast_errors[-1] if fast_errors else "The fast-tap hotkey did not register.")
                )

            fast_started = time.monotonic()
            fast_tap_ms = _tap_keys_fast(sorted(parse_hotkey(settings.hotkey).keys))
            callbacks_finished = _pump_until(app, lambda: len(fast_trace) >= 2, 3.0)
            fast_terminal = _pump_until(
                app,
                lambda: (
                    callbacks_finished
                    and len(session_events) > fast_event_start
                    and engine.state in {SessionState.COMPLETE, SessionState.ERROR}
                ),
                FAST_TAP_SETTLE_TIMEOUT_SECONDS,
            )
            fast_events = session_events[fast_event_start:]
            fast_states = [event.state.value for event in fast_events]
            with fast_trace_lock:
                fast_trace_snapshot = list(fast_trace)
            fast_actions = [str(item["action"]) for item in fast_trace_snapshot]
            fast_threads = [int(item["thread_id"]) for item in fast_trace_snapshot]
            try:
                listening_index = fast_states.index(SessionState.LISTENING.value)
                transcribing_index = fast_states.index(SessionState.TRANSCRIBING.value)
                complete_index = len(fast_states) - 1 - fast_states[::-1].index(
                    SessionState.COMPLETE.value
                )
                fast_states_ordered = listening_index < transcribing_index < complete_index
            except ValueError:
                fast_states_ordered = False
            _pump_until(
                app,
                lambda: str(_read_target_status(status_path).get("text") or "")
                != fast_target_before,
                0.35,
            )
            fast_target_after = str(_read_target_status(status_path).get("text") or "")
            details["fast_tap"] = {
                "hotkey": settings.hotkey,
                "physical_tap_ms": round(fast_tap_ms, 3),
                "maximum_tap_ms": FAST_TAP_MAX_MS,
                "press_callback_delay_ms": round(FAST_TAP_PRESS_DELAY_SECONDS * 1000.0),
                "callback_trace": fast_trace_snapshot,
                "session_states": fast_states,
                "initial_state": fast_initial_state,
                "final_state": engine.state.value,
                "engine_busy": engine.is_busy,
                "recorder_started": bool(engine.recorder.started),
                "insertion_enabled": bool(engine.settings.insert_text),
                "target_unchanged": fast_target_after == fast_target_before,
                "registration_error": listener.registration_error
                or (fast_errors[-1] if fast_errors else ""),
            }
            checks["fast_tap_is_short"] = fast_tap_ms <= FAST_TAP_MAX_MS
            checks["fast_tap_press_before_release"] = bool(
                callbacks_finished and fast_actions == ["press", "release"]
            )
            checks["fast_tap_callbacks_serialized"] = bool(
                len(fast_threads) == 2 and len(set(fast_threads)) == 1
            )
            checks["fast_tap_session_ordered"] = bool(
                fast_terminal and fast_states_ordered
            )
            checks["fast_tap_not_stuck"] = bool(
                fast_terminal
                and engine.state is SessionState.COMPLETE
                and not engine.is_busy
                and not engine.recorder.started
            )
            checks["fast_tap_insertion_disabled"] = bool(
                not engine.settings.insert_text and fast_target_after == fast_target_before
            )

            checks["no_recorded_audio_persisted"] = not any(
                path.name.startswith("record") and path.suffix.casefold() == ".wav"
                for path in temporary.rglob("*.wav")
            )
            worker = engine._worker
            if worker is not None and worker.is_alive():
                worker.join(timeout=3.0)
            window.shutdown()
            gc.collect()
    except _PackagedExactWorkflowComplete:
        pass
    except Exception as exc:
        evidence["error"] = str(exc)
    finally:
        if keys:
            _release_keys(keys)
        if listener is not None:
            listener.stop()
            listener.join(timeout=1.5)
        sound_cues._play_blocking = original_play_blocking
        if window is not None:
            window.shutdown()
            window.close()
            if app is not None:
                app.processEvents()
        elif transcriber is not None:
            transcriber.release_model()
        if target_process is not None:
            if target_handle:
                ctypes.windll.user32.PostMessageW(target_handle, 0x0010, 0, 0)
            if target_process.poll() is None:
                try:
                    target_process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    target_process.terminate()
                    try:
                        target_process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        target_process.kill()
                        target_process.wait(timeout=2)
            if target_process.stderr is not None:
                target_process.stderr.close()
        if temporary_path is not None and temporary_path.exists():
            for _attempt in range(5):
                shutil.rmtree(temporary_path, ignore_errors=True)
                if not temporary_path.exists():
                    break
                gc.collect()
                time.sleep(0.1)

    required_checks = PACKAGED_REQUIRED_CHECKS if packaged_mode else SOURCE_REQUIRED_CHECKS
    evidence["required_checks"] = list(required_checks)
    evidence["passed"] = (
        not evidence.get("error")
        and all(checks.get(name) is True for name in required_checks)
    )
    evidence["finished_at"] = _utc_now()
    return evidence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Drive Manuscript's Windows hotkey, cue, HUD, local transcription, and "
            "cross-process insertion path without human speech."
        )
    )
    parser.add_argument("--hotkey", default=DEFAULT_PROBE_HOTKEY)
    parser.add_argument("--phrase", default="Echo Link workflow test")
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--skip-microphone", action="store_true")
    parser.add_argument("--packaged-mode", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--target-mode", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--target-title", default="", help=argparse.SUPPRESS)
    parser.add_argument("--target-status", type=Path, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.target_mode:
        if not args.target_title or args.target_status is None:
            return 2
        return _run_target(args.target_title, args.target_status)
    report = run_probe(args)
    if args.output:
        _write_json_atomic(args.output, report)
    if sys.stdout is not None:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
