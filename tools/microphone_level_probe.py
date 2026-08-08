"""Record a few seconds from each candidate microphone and report the level.

"No speech was detected" means the stream opened and returned silence, which
selection logic cannot explain. This measures what each device actually hears,
so a muted or zero-level input is told apart from a wrong device.

    .venv/Scripts/python.exe tools/microphone_level_probe.py
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
import sounddevice as sd  # noqa: E402

SECONDS = 4.0
CANDIDATES = (1, 8, 17, 20)


def main() -> int:
    print(f"Speak normally for the next {SECONDS:.0f} seconds per device.\n")
    for index in CANDIDATES:
        try:
            name = str(sd.query_devices(index)["name"])
        except Exception as exc:  # noqa: BLE001 - report and keep going
            print(f"[{index}] unavailable: {exc}")
            continue
        try:
            audio = sd.rec(
                int(SECONDS * 16000),
                samplerate=16000,
                channels=1,
                dtype="int16",
                device=index,
            )
            sd.wait()
        except Exception as exc:  # noqa: BLE001 - a dead device is the finding
            print(f"[{index}] {name[:44]:<44} could not record: {exc}")
            continue
        samples = np.asarray(audio, dtype=np.float64).reshape(-1)
        peak = float(np.max(np.abs(samples))) if samples.size else 0.0
        rms = float(np.sqrt(np.mean(samples**2))) if samples.size else 0.0
        verdict = "SILENT" if peak < 200 else ("quiet" if peak < 2000 else "OK")
        print(f"[{index}] {name[:44]:<44} peak={peak:>7.0f} rms={rms:>7.0f}  {verdict}")
    print("\npeak is out of 32767. Under 200 means the device heard nothing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
