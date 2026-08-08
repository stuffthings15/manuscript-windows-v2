"""Measure what MicrophoneRecorder returns, the way the app calls it.

sd.rec on the same device gives a healthy peak while the app reports "No
speech was detected", so the fault is between the device and the transcriber.
This exercises the real class - arm, start, stop - and also records the same
window with plain sounddevice for comparison.

    .venv/Scripts/python.exe tools/recorder_path_probe.py
"""
from __future__ import annotations

from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
import sounddevice as sd  # noqa: E402

from echo_link_core.audio import MicrophoneRecorder, resolve_microphone  # noqa: E402


def level(samples) -> str:
    data = np.asarray(samples, dtype=np.float64).reshape(-1)
    if not data.size:
        return "NO SAMPLES AT ALL"
    peak = float(np.max(np.abs(data)))
    rms = float(np.sqrt(np.mean(data**2)))
    verdict = "SILENT" if peak < 200 else ("quiet" if peak < 2000 else "OK")
    return f"samples={data.size:>6} peak={peak:>7.0f} rms={rms:>7.0f}  {verdict}"


def main() -> int:
    device = resolve_microphone(None, "")
    print(f"resolved device = {device} (None means system default)\n")

    recorder = MicrophoneRecorder()
    print("ARMED for pre-roll, as the app does when the caret lands.")
    recorder.arm(device, 500)
    time.sleep(1.0)

    print("SPEAK NOW for 4 seconds ...")
    recorder.start(device)
    time.sleep(4.0)
    captured = recorder.stop()
    print(f"  MicrophoneRecorder: {level(captured)}")

    print("\nSPEAK AGAIN for 4 seconds (plain sounddevice, for comparison) ...")
    reference = sd.rec(4 * 16000, samplerate=16000, channels=1,
                       dtype="int16", device=device)
    sd.wait()
    print(f"  plain sounddevice : {level(reference)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
