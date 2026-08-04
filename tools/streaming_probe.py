from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from echo_link_core.models import DictationSettings
from echo_link_core.transcription import LocalWhisperTranscriber


def read_wave(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as source:
        channels = source.getnchannels()
        sample_width = source.getsampwidth()
        sample_rate = source.getframerate()
        frames = source.readframes(source.getnframes())
    if sample_width != 2:
        raise ValueError("Streaming probe requires 16-bit PCM WAV audio.")
    audio = np.frombuffer(frames, dtype=np.int16)
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1).astype(np.int16)
    if sample_rate != 16000:
        source_positions = np.arange(audio.size, dtype=np.float64)
        target_size = max(1, round(audio.size * 16000 / sample_rate))
        target_positions = np.linspace(0, max(0, audio.size - 1), target_size)
        audio = np.interp(target_positions, source_positions, audio).astype(np.int16)
    return audio


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe live local partial transcription")
    parser.add_argument("wav", type=Path)
    args = parser.parse_args(argv)
    transcriber = LocalWhisperTranscriber(DictationSettings(model_name="base.en"))
    try:
        text = transcriber.transcribe_partial(read_wave(args.wav))
    finally:
        transcriber.release_model()
    print(f"partial={text!r}")
    return 0 if text else 1


if __name__ == "__main__":
    raise SystemExit(main())
