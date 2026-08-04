from __future__ import annotations

import io
import logging
import os
import sys
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import sounddevice as sd

from echo_link_core.data_protection import (
    UnsafePathError,
    validate_local_data_file,
    validate_local_directory,
)


logger = logging.getLogger(__name__)
_playback_lock = threading.Lock()

BUILTIN_START = "Built-in activation tone"
BUILTIN_STOP = "Built-in completion tone"

BLUETOOTH_KEYWORDS = (
    "bluetooth",
    "airpods",
    "headset",
    "headphones",
    "earbuds",
    "wireless",
    "a2dp",
    "hands-free",
    "handsfree",
    "jabra",
    "bose",
    "sony wh",
    "sony wf",
    "jbl",
    "beats",
    "galaxy buds",
    "pixel buds",
    "sennheiser",
    "soundcore",
    "anker",
)


@dataclass(frozen=True, slots=True)
class AudioOutputInfo:
    index: int
    name: str
    channels: int
    default_sample_rate: int


def list_audio_outputs() -> list[AudioOutputInfo]:
    try:
        devices = sd.query_devices()
    except Exception as exc:
        logger.warning("Audio output query failed: %s", exc)
        return []
    result: list[AudioOutputInfo] = []
    for index, device in enumerate(devices):
        channels = int(device.get("max_output_channels", 0) or 0)
        if channels <= 0:
            continue
        result.append(
            AudioOutputInfo(
                index=index,
                name=str(device.get("name") or f"Output {index}"),
                channels=channels,
                default_sample_rate=int(device.get("default_samplerate") or 44100),
            )
        )
    return result


def list_sound_choices() -> list[str]:
    try:
        media = validate_local_directory(
            Path(os.environ.get("WINDIR", r"C:\Windows")) / "Media",
            purpose="Windows sound folder",
            create=False,
        )
    except (UnsafePathError, FileNotFoundError, OSError):
        return [BUILTIN_START, BUILTIN_STOP]
    return [
        BUILTIN_START,
        BUILTIN_STOP,
        *sorted((path.name for path in media.glob("*.wav")), key=str.casefold),
    ]


def resolve_cue_path(reference: str | None, default_name: str) -> Path | None:
    value = str(reference or default_name).strip()
    if not value or value.casefold() == "muted":
        return None
    if value in {BUILTIN_START, BUILTIN_STOP}:
        return None
    direct = Path(value).expanduser()
    if direct.parent != Path("."):
        try:
            direct_path = validate_local_data_file(
                direct,
                purpose="Sound cue",
                allowed_suffixes={".wav"},
            )
        except (UnsafePathError, FileNotFoundError, OSError):
            return None
        return direct_path if direct_path.is_file() else None
    try:
        media_path = validate_local_data_file(
            Path(os.environ.get("WINDIR", r"C:\Windows")) / "Media" / direct.name,
            purpose="Sound cue",
            allowed_suffixes={".wav"},
        )
    except (UnsafePathError, FileNotFoundError, OSError):
        return None
    return media_path if media_path.is_file() else None


def output_device_name(output_index: int | None) -> str:
    try:
        device = sd.query_devices(output_index, "output") if output_index is not None else sd.query_devices(kind="output")
        return str(device.get("name") or "")
    except Exception:
        return ""


def resolve_delay_ms(configured_delay_ms: int, device_name: str) -> int:
    value = int(configured_delay_ms)
    if value >= 0:
        return value
    normalized = str(device_name or "").casefold()
    return 700 if any(keyword in normalized for keyword in BLUETOOTH_KEYWORDS) else 0


def _read_wave(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as source:
        channels = source.getnchannels()
        sample_width = source.getsampwidth()
        sample_rate = source.getframerate()
        frames = source.readframes(source.getnframes())
    if sample_width == 1:
        data = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sample_width == 2:
        data = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    elif sample_width == 3:
        packed = np.frombuffer(frames, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        values = packed[:, 0] | (packed[:, 1] << 8) | (packed[:, 2] << 16)
        values = np.where(values & 0x800000, values - 0x1000000, values)
        data = values.astype(np.float32) / 8388608.0
    elif sample_width == 4:
        data = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"Unsupported WAV sample width: {sample_width * 8}-bit")
    if channels > 1:
        data = data.reshape(-1, channels)
    return np.ascontiguousarray(data, dtype=np.float32), int(sample_rate)


def _play_blocking(path: Path, output_index: int | None, delay_ms: int) -> bool:
    try:
        data, sample_rate = _read_wave(path)
        channels = 1 if data.ndim == 1 else data.shape[1]
        with _playback_lock:
            if delay_ms > 0:
                silence_frames = max(1, int(sample_rate * 0.1))
                silence = np.zeros((silence_frames, channels), dtype=np.float32)
                sd.play(silence, sample_rate, device=output_index, blocking=True)
                time.sleep(max(0.0, (delay_ms - 100) / 1000.0))
            sd.play(data, sample_rate, device=output_index, blocking=True)
        return True
    except Exception as exc:
        logger.warning("Routed cue playback failed for %s: %s", path, exc)
        if output_index is not None or sys.platform != "win32":
            return False
        try:
            import winsound

            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_NODEFAULT)
            return True
        except Exception:
            logger.warning("Default Windows cue playback failed for %s", path, exc_info=True)
            return False


def _builtin_kind(reference: str | None, default_name: str) -> str | None:
    value = str(reference or default_name).strip().casefold()
    if value in {BUILTIN_START.casefold(), "speech on.wav"}:
        return "start"
    if value in {BUILTIN_STOP.casefold(), "speech off.wav"}:
        return "stop"
    return None


def _builtin_tone(kind: str, sample_rate: int = 44_100) -> np.ndarray:
    count = max(1, int(sample_rate * 0.16))
    split = count // 2
    frequencies = np.empty(count, dtype=np.float32)
    if kind == "start":
        frequencies[:split], frequencies[split:] = 660.0, 880.0
    else:
        frequencies[:split], frequencies[split:] = 740.0, 520.0
    phase = 2.0 * np.pi * np.cumsum(frequencies) / float(sample_rate)
    envelope = np.ones(count, dtype=np.float32)
    fade = max(1, int(sample_rate * 0.018))
    envelope[:fade] = np.linspace(0.0, 1.0, fade, dtype=np.float32)
    envelope[-fade:] = np.linspace(1.0, 0.0, fade, dtype=np.float32)
    return np.ascontiguousarray(0.22 * np.sin(phase) * envelope, dtype=np.float32)


def _tone_wave_bytes(data: np.ndarray, sample_rate: int) -> bytes:
    pcm = (np.clip(data, -1.0, 1.0) * 32767.0).astype("<i2")
    target = io.BytesIO()
    with wave.open(target, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm.tobytes())
    return target.getvalue()


def _play_builtin_blocking(kind: str, output_index: int | None, delay_ms: int) -> bool:
    sample_rate = 44_100
    data = _builtin_tone(kind, sample_rate)
    try:
        with _playback_lock:
            if delay_ms > 0:
                silence_frames = max(1, int(sample_rate * 0.1))
                silence = np.zeros(silence_frames, dtype=np.float32)
                sd.play(silence, sample_rate, device=output_index, blocking=True)
                time.sleep(max(0.0, (delay_ms - 100) / 1000.0))
            sd.play(data, sample_rate, device=output_index, blocking=True)
        return True
    except Exception:
        if output_index is not None or sys.platform != "win32":
            return False
        try:
            import winsound

            winsound.PlaySound(
                _tone_wave_bytes(data, sample_rate),
                winsound.SND_MEMORY | winsound.SND_NODEFAULT,
            )
            return True
        except Exception:
            logger.warning("Built-in cue playback failed", exc_info=True)
            return False


def play_cue(
    reference: str | None,
    *,
    default_name: str,
    output_index: int | None = None,
    delay_ms: int = -1,
    blocking: bool = False,
) -> bool:
    if os.environ.get("ECHOLINK_DISABLE_SOUNDS") == "1":
        return False
    builtin_kind = _builtin_kind(reference, default_name)
    path = resolve_cue_path(reference, default_name)
    if path is None and builtin_kind is None:
        return False
    resolved_delay = resolve_delay_ms(delay_ms, output_device_name(output_index))
    if path is not None:
        target, target_args = _play_blocking, (path, output_index, resolved_delay)
    else:
        target, target_args = (
            _play_builtin_blocking,
            (builtin_kind, output_index, resolved_delay),
        )
    if blocking:
        return target(*target_args)
    threading.Thread(
        target=target,
        args=target_args,
        daemon=True,
        name="echo-link-sound-cue",
    ).start()
    return True


def play_start(settings=None, *, blocking: bool = False) -> bool:
    return play_cue(
        getattr(settings, "start_sound", BUILTIN_START),
        default_name=BUILTIN_START,
        output_index=getattr(settings, "audio_output_index", None),
        delay_ms=getattr(settings, "bluetooth_delay_ms", -1),
        blocking=blocking,
    )


def play_stop(settings=None, *, blocking: bool = False) -> bool:
    return play_cue(
        getattr(settings, "stop_sound", BUILTIN_STOP),
        default_name=BUILTIN_STOP,
        output_index=getattr(settings, "audio_output_index", None),
        delay_ms=0,
        blocking=blocking,
    )
