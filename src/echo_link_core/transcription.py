from __future__ import annotations

import gc
import logging
import os
import sys
import threading
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from .models import DictationMode, DictationSettings
from .paths import model_root, user_model_root


logger = logging.getLogger(__name__)


class ModelUnavailableError(RuntimeError):
    pass


class TranscriptionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LocalModelInfo:
    name: str
    path: Path
    multilingual: bool


def discover_local_models(search_roots: list[Path] | None = None) -> list[LocalModelInfo]:
    roots = search_roots or [model_root(), user_model_root()]
    found: dict[str, LocalModelInfo] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for candidate in sorted(root.iterdir()):
            if not _valid_model_directory(candidate):
                continue
            name = candidate.name
            found.setdefault(
                name,
                LocalModelInfo(
                    name=name,
                    path=candidate,
                    multilingual=not name.casefold().endswith(".en"),
                ),
            )
    return sorted(found.values(), key=lambda item: item.name.casefold())


def local_model_path(model_name: str) -> Path | None:
    for root in (model_root(), user_model_root()):
        candidate = root / model_name
        if _valid_model_directory(candidate):
            return candidate

    cache_root = (
        Path.home()
        / ".cache"
        / "huggingface"
        / "hub"
        / f"models--Systran--faster-whisper-{model_name}"
        / "snapshots"
    )
    if cache_root.exists():
        candidates = sorted(cache_root.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True)
        for candidate in candidates:
            if _valid_model_directory(candidate):
                return candidate
    return None


def _valid_model_directory(path: Path) -> bool:
    return path.is_dir() and (path / "model.bin").is_file() and (path / "config.json").is_file()


def cuda_available() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


class _ComAndGcGuard(AbstractContextManager):
    def __enter__(self):
        self._gc_enabled = gc.isenabled()
        self._collect = gc.collect
        gc.disable()
        gc.collect = lambda *args, **kwargs: 0
        self._com_active = False
        if sys.platform == "win32":
            try:
                import ctypes

                result = ctypes.windll.ole32.CoInitializeEx(None, 0x2)
                self._com_active = result in (0, 1)
            except Exception:
                self._com_active = False
        return self

    def __exit__(self, *_exc):
        if self._com_active:
            try:
                import ctypes

                ctypes.windll.ole32.CoUninitialize()
            except Exception:
                pass
        gc.collect = self._collect
        if self._gc_enabled:
            gc.enable()
        return False


class LocalWhisperTranscriber:
    def __init__(self, settings: DictationSettings | None = None):
        self._settings = settings or DictationSettings()
        self._model = None
        self._model_key: tuple[str, str, str] | None = None
        self._lock = threading.RLock()
        self._inference_lock = threading.Lock()
        self._last_used = 0.0
        # How many times the speech model has actually been loaded. A reload
        # is the difference between an instant dictation and a slow one, and
        # nothing recorded it.
        self._model_loads = 0
        self._release_timer: threading.Timer | None = None

    def update_settings(self, settings: DictationSettings) -> None:
        new_key = self._effective_key(settings)
        with self._lock:
            old_key = self._effective_key(self._settings)
            self._settings = settings
        if new_key != old_key:
            self.release_model()

    def status(self) -> dict[str, object]:
        settings = self._settings
        path = local_model_path(settings.model_name)
        device = settings.device.lower()
        if device == "cuda" and not cuda_available():
            device = "cpu"
        with self._lock:
            loaded = self._model is not None
        return {
            "model": settings.model_name,
            "path": str(path) if path else "",
            "available": path is not None,
            "device": device,
            "loaded": loaded,
        }

    def warmup(self, status_callback: Callable[[str], None] | None = None) -> None:
        self._get_model(status_callback)

    def benchmark(self, duration_seconds: float = 2.0) -> dict[str, float | str]:
        duration = max(1.0, min(float(duration_seconds), 5.0))
        sample_count = int(16_000 * duration)
        timeline = np.arange(sample_count, dtype=np.float32) / 16_000.0
        audio = (0.02 * np.sin(2.0 * np.pi * 180.0 * timeline)).astype(np.float32)
        load_started = time.monotonic()
        model = self._get_model()
        load_seconds = time.monotonic() - load_started
        language = None if self._settings.language.lower() == "auto" else self._settings.language
        inference_started = time.monotonic()
        try:
            with self._inference_lock, _ComAndGcGuard():
                segments, _info = model.transcribe(
                    audio,
                    language=language,
                    beam_size=1,
                    best_of=1,
                    vad_filter=False,
                    condition_on_previous_text=False,
                    temperature=0.0,
                )
                list(segments)
        except Exception as exc:
            self.release_model()
            raise TranscriptionError(f"Local speed check failed: {exc}") from exc
        inference_seconds = time.monotonic() - inference_started
        return {
            "model": self._settings.model_name,
            "device": self._effective_key(self._settings)[1],
            "load_seconds": round(load_seconds, 3),
            "inference_seconds": round(inference_seconds, 3),
            "realtime_factor": round(inference_seconds / duration, 3),
        }

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        mode: DictationMode | None = None,
        status_callback: Callable[[str], None] | None = None,
    ) -> str:
        array = _prepare_audio(audio, whisper_mode=self._settings.whisper_mode)
        if array.size < 4000:
            raise TranscriptionError("No clear speech was recorded. Try again and speak a little longer.")

        started = time.monotonic()
        loads_before = self._model_loads
        model = self._get_model(status_callback)
        model_ready = time.monotonic()
        selected_mode = mode or self._settings.mode
        beam_size = 3 if selected_mode is DictationMode.ACCURACY else 1
        language = None if self._settings.language.lower() == "auto" else self._settings.language
        def run(vad_filter: bool) -> str:
            segments, _info = model.transcribe(
                array,
                language=language,
                beam_size=beam_size,
                best_of=beam_size,
                vad_filter=vad_filter,
                vad_parameters=(
                    _vad_parameters(self._settings.whisper_mode) if vad_filter else None
                ),
                condition_on_previous_text=False,
                temperature=0.0,
            )
            return " ".join(
                segment.text.strip() for segment in segments if segment.text.strip()
            ).strip()

        try:
            with self._inference_lock, _ComAndGcGuard():
                result = run(True)
                if not result:
                    # The voice filter discarded the whole recording. Measured
                    # from Curtis's log on 2026-08-21: it removed all 1.25
                    # seconds of a 1.25 second clip and the dictation failed
                    # with "No speech was detected", and it removed 2.55 of
                    # 2.85 seconds on another.
                    #
                    # The audio is already in hand, so the words are still
                    # there to be had. Handing the writer nothing and making
                    # them say it again is the worst available outcome, and it
                    # is what "extremely inconsistent" felt like.
                    #
                    # A retry is deterministic. Lowering the threshold would be
                    # a guess tuned to one sample, and the default profile does
                    # not even set one - it inherits faster-whisper's 0.5,
                    # which is what is strict here.
                    logger.info(
                        "Dictation voice filter removed everything; "
                        "transcribing the recording again without it "
                        "audio_seconds=%.1f",
                        float(array.size) / 16000.0,
                    )
                    result = run(False)
        except Exception as exc:
            self.release_model()
            raise TranscriptionError(f"Speech recognition failed: {exc}") from exc
        self._last_used = time.monotonic()
        # What a slow dictation was actually spent on. Curtis reported
        # dictation "taking over a minute" and being "extremely inconsistent"
        # on 2026-08-21, and the log could not tell a model reload from a slow
        # transcription from GPU contention: it recorded that a transcription
        # had started and nothing more.
        #
        # Durations and the device only. The words are never logged.
        key = self._model_key or ("unknown", "unknown", "unknown")
        logger.info(
            "Dictation transcription finished seconds=%.3f load_seconds=%.3f "
            "audio_seconds=%.1f device=%s model=%s reloaded=%s",
            self._last_used - started,
            model_ready - started,
            float(array.size) / 16000.0,
            key[1],
            key[0],
            self._model_loads > loads_before,
        )
        self._schedule_gpu_release()
        if not result:
            raise TranscriptionError("No speech was detected. Check the microphone and try again.")
        return result

    def transcribe_partial(self, audio: np.ndarray) -> str:
        array = _prepare_audio(audio, whisper_mode=self._settings.whisper_mode)
        if array.size < 8000:
            return ""
        model = self._get_model()
        language = None if self._settings.language.lower() == "auto" else self._settings.language
        try:
            with self._inference_lock, _ComAndGcGuard():
                segments, _info = model.transcribe(
                    array,
                    language=language,
                    beam_size=1,
                    best_of=1,
                    vad_filter=True,
                    vad_parameters=_vad_parameters(self._settings.whisper_mode),
                    condition_on_previous_text=False,
                    temperature=0.0,
                )
                result = " ".join(
                    segment.text.strip() for segment in segments if segment.text.strip()
                ).strip()
        except Exception as exc:
            logger.debug("Live partial transcription failed: %s", exc)
            return ""
        self._last_used = time.monotonic()
        self._schedule_gpu_release()
        return result

    def _effective_key(self, settings: DictationSettings) -> tuple[str, str, str]:
        device = settings.device.lower()
        compute = settings.compute_type
        if device == "cuda" and not cuda_available():
            device, compute = "cpu", "int8"
        return settings.model_name, device, compute

    def _get_model(self, status_callback: Callable[[str], None] | None = None):
        key = self._effective_key(self._settings)
        with self._lock:
            if self._model is not None and self._model_key == key:
                return self._model
            source = local_model_path(key[0])
            if source is None:
                raise ModelUnavailableError(
                    f"The local {key[0]} speech model is not installed. Open Settings to repair the model."
                )
            if status_callback:
                status_callback(f"Loading {key[0]} on {key[1].upper()}...")
            load_started = time.monotonic()
            try:
                from faster_whisper import WhisperModel

                cpu_threads = max(2, min(os.cpu_count() or 4, 8))
                self._model = WhisperModel(
                    str(source),
                    device=key[1],
                    compute_type=key[2],
                    cpu_threads=cpu_threads,
                    local_files_only=True,
                )
                self._model_key = key
                self._model_loads += 1
                logger.info(
                    "Dictation speech model loaded seconds=%.3f device=%s "
                    "compute=%s model=%s load_count=%s",
                    time.monotonic() - load_started,
                    key[1],
                    key[2],
                    key[0],
                    self._model_loads,
                )
            except Exception as exc:
                self._model = None
                self._model_key = None
                raise ModelUnavailableError(f"The local speech model could not load: {exc}") from exc
            return self._model

    def _schedule_gpu_release(self) -> None:
        with self._lock:
            if self._model_key is None or self._model_key[1] != "cuda":
                return
            if self._release_timer is not None:
                self._release_timer.cancel()
            timeout = max(30, int(self._settings.gpu_idle_release_seconds))
            timer = threading.Timer(timeout, self._release_if_idle, args=(timeout,))
            timer.daemon = True
            self._release_timer = timer
            timer.start()

    def _release_if_idle(self, timeout: int) -> None:
        if time.monotonic() - self._last_used >= timeout:
            self.release_model()

    def release_model(self) -> None:
        with self._inference_lock:
            with self._lock:
                if self._release_timer is not None:
                    self._release_timer.cancel()
                    self._release_timer = None
                released = self._model is not None
                key = self._model_key or ("unknown", "unknown", "unknown")
                self._model = None
                self._model_key = None
        if released:
            # The next dictation will pay a full reload, and until now the log
            # gave no sign that this had happened at all.
            logger.info(
                "Dictation speech model released device=%s model=%s "
                "idle_seconds=%.1f",
                key[1],
                key[0],
                max(0.0, time.monotonic() - self._last_used),
            )
        gc.collect()


def _prepare_audio(audio: np.ndarray, *, whisper_mode: bool) -> np.ndarray:
    array = np.asarray(audio).reshape(-1)
    if array.dtype != np.float32:
        array = array.astype(np.float32)
    if np.max(np.abs(array), initial=0.0) > 1.5:
        array /= 32768.0
    if whisper_mode and array.size:
        peak = float(np.max(np.abs(array), initial=0.0))
        if 0.0005 < peak < 0.25:
            array = np.clip(array * min(4.0, 0.25 / peak), -1.0, 1.0)
    return array


def _vad_parameters(whisper_mode: bool) -> dict[str, int | float]:
    if whisper_mode:
        return {
            "threshold": 0.25,
            "min_speech_duration_ms": 80,
            "min_silence_duration_ms": 450,
            "speech_pad_ms": 300,
        }
    return {
        "min_speech_duration_ms": 150,
        "min_silence_duration_ms": 300,
        "speech_pad_ms": 250,
    }
