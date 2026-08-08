from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass
from typing import Callable

import numpy as np
import sounddevice as sd


logger = logging.getLogger(__name__)


class AudioCaptureError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MicrophoneInfo:
    index: int
    name: str
    default_sample_rate: int


def list_microphones() -> list[MicrophoneInfo]:
    try:
        devices = sd.query_devices()
    except Exception as exc:
        logger.warning("Microphone query failed: %s", exc)
        return []
    result: list[MicrophoneInfo] = []
    for index, device in enumerate(devices):
        if int(device.get("max_input_channels", 0)) <= 0:
            continue
        result.append(
            MicrophoneInfo(
                index=index,
                name=str(device.get("name") or f"Microphone {index}"),
                default_sample_rate=int(device.get("default_samplerate") or 16000),
            )
        )
    return result


def resolve_microphone(index: int | None, name: str = "") -> int | None:
    """Turn a saved microphone choice into an index that is valid right now.

    sounddevice indices are positional, so they move whenever a device appears
    or disappears: a Bluetooth headset connecting or disconnecting renumbers
    every device after it. A saved index then points at a different microphone,
    or at nothing at all, and the stream fails to open - the app reports no
    microphone while a healthy built-in array sits unused. The name is the
    stable half of the choice, so it decides, and anything unresolvable falls
    back to the system default rather than to failure.
    """
    devices = list_microphones()
    if not devices:
        return None
    wanted = (name or "").strip().casefold()
    if wanted:
        for device in devices:
            if device.name.strip().casefold() == wanted:
                return device.index
        # The chosen device is not connected right now. Its old index belongs to
        # some other microphone today, so using it would record the wrong one.
        return None
    if index is None:
        return None
    # A settings file written before names were stored. Honour the bare index
    # only while it still points at an input device.
    return int(index) if any(device.index == int(index) for device in devices) else None


def play_pcm16(audio_pcm: bytes, sample_rate: int, device_index: int | None = None) -> None:
    """Play decrypted speech-log audio from memory without creating a file."""
    audio = np.frombuffer(audio_pcm, dtype=np.int16)
    if not audio.size:
        raise AudioCaptureError("The saved recording is empty.")
    try:
        sd.stop()
        sd.play(audio, samplerate=int(sample_rate), device=device_index, blocking=False)
    except Exception as exc:
        raise AudioCaptureError(f"Could not play the saved recording: {exc}") from exc


def stop_audio_playback() -> None:
    try:
        sd.stop()
    except Exception:
        logger.debug("Audio playback stop failed", exc_info=True)


class MicrophoneRecorder:
    def __init__(self, sample_rate: int = 16000, block_ms: int = 50):
        self.sample_rate = int(sample_rate)
        self.block_size = max(160, int(self.sample_rate * block_ms / 1000))
        self._stream: sd.InputStream | None = None
        self._frames: list[np.ndarray] = []
        self._recording = False
        self._lock = threading.RLock()
        self._level_callback: Callable[[float], None] | None = None
        self._armed = False
        self._device_index: int | None = None
        self._pre_roll_samples = 0
        self._pre_roll: deque[np.ndarray] = deque()
        self._pre_roll_size = 0

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._recording

    def start(
        self,
        device_index: int | None = None,
        level_callback: Callable[[float], None] | None = None,
    ) -> None:
        with self._lock:
            if self._recording:
                return
            if self._armed and self._stream is not None and device_index == self._device_index:
                self._frames = [block.copy() for block in self._pre_roll]
                self._pre_roll.clear()
                self._pre_roll_size = 0
                self._level_callback = level_callback
                self._recording = True
                return
            old_stream = self._stream
            self._stream = None
            self._armed = False
            self._pre_roll.clear()
            self._pre_roll_size = 0
            self._frames = []
            self._level_callback = level_callback
            self._recording = True
            self._device_index = device_index
        self._close_stream(old_stream)
        stream = None
        try:
            stream = sd.InputStream(
                samplerate=self.sample_rate,
                blocksize=self.block_size,
                device=device_index,
                channels=1,
                dtype="int16",
                callback=self._callback,
            )
            stream.start()
            with self._lock:
                self._stream = stream
        except Exception as exc:
            try:
                self._close_stream(stream)
            except Exception:
                logger.warning("Microphone stream cleanup after start failure failed", exc_info=True)
            with self._lock:
                self._recording = False
                self._stream = None
            raise AudioCaptureError(f"Could not start the microphone: {exc}") from exc

    def arm(self, device_index: int | None = None, pre_roll_ms: int = 500) -> None:
        """Keep a short, memory-only lead-in buffer so first words are not clipped."""
        samples = max(0, int(self.sample_rate * int(pre_roll_ms) / 1000))
        with self._lock:
            if self._recording:
                return
            if samples > 0 and self._armed and self._stream is not None and device_index == self._device_index:
                self._pre_roll_samples = samples
                self._trim_pre_roll_locked()
                return
            old_stream = self._stream
            self._stream = None
            self._armed = False
            self._device_index = device_index
            self._pre_roll_samples = samples
            self._pre_roll.clear()
            self._pre_roll_size = 0
        self._close_stream(old_stream)
        if samples <= 0:
            return
        stream = None
        try:
            stream = sd.InputStream(
                samplerate=self.sample_rate,
                blocksize=self.block_size,
                device=device_index,
                channels=1,
                dtype="int16",
                callback=self._callback,
            )
            stream.start()
            with self._lock:
                self._stream = stream
                self._armed = True
        except Exception as exc:
            try:
                self._close_stream(stream)
            except Exception:
                logger.warning("Pre-roll stream cleanup after start failure failed", exc_info=True)
            with self._lock:
                self._stream = None
                self._armed = False
            raise AudioCaptureError(f"Could not ready the microphone: {exc}") from exc

    def _callback(self, input_data, _frames, _time_info, status) -> None:
        if status:
            logger.warning("Microphone status: %s", status)
        block = np.asarray(input_data, dtype=np.int16).reshape(-1).copy()
        with self._lock:
            if not self._recording and not self._armed:
                return
            if self._recording:
                self._frames.append(block)
                callback = self._level_callback
            else:
                self._pre_roll.append(block)
                self._pre_roll_size += block.size
                self._trim_pre_roll_locked()
                callback = None
        if callback is not None and block.size:
            normalized = block.astype(np.float32) / 32768.0
            rms = float(np.sqrt(np.mean(np.square(normalized))))
            try:
                callback(min(1.0, rms * 8.0))
            except Exception:
                logger.debug("Microphone level callback failed", exc_info=True)

    def stop(self) -> np.ndarray:
        with self._lock:
            if not self._recording:
                return np.zeros(0, dtype=np.int16)
            self._recording = False
            stream = self._stream
            keep_stream = self._armed
            if not keep_stream:
                self._stream = None
        if stream is not None and not keep_stream:
            self._close_stream(stream)
        with self._lock:
            frames = self._frames
            self._frames = []
            self._level_callback = None
        if not frames:
            return np.zeros(0, dtype=np.int16)
        return np.concatenate(frames).astype(np.int16, copy=False)

    def snapshot(self) -> np.ndarray:
        with self._lock:
            frames = tuple(self._frames)
        if not frames:
            return np.zeros(0, dtype=np.int16)
        return np.concatenate(frames).astype(np.int16, copy=False)

    def cancel(self) -> None:
        with self._lock:
            self._recording = False
            self._armed = False
            stream = self._stream
            self._stream = None
            self._frames = []
            self._pre_roll.clear()
            self._pre_roll_size = 0
            self._level_callback = None
        try:
            self._close_stream(stream)
        except Exception:
            logger.warning("Microphone cleanup failed", exc_info=True)

    def _trim_pre_roll_locked(self) -> None:
        while self._pre_roll and self._pre_roll_size > self._pre_roll_samples:
            excess = self._pre_roll_size - self._pre_roll_samples
            first = self._pre_roll[0]
            if first.size <= excess:
                self._pre_roll.popleft()
                self._pre_roll_size -= first.size
            else:
                self._pre_roll[0] = first[excess:].copy()
                self._pre_roll_size -= excess

    @staticmethod
    def _close_stream(stream) -> None:
        if stream is None:
            return
        try:
            stream.stop()
        finally:
            stream.close()
