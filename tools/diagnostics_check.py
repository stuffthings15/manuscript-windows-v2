from __future__ import annotations

import json
from pathlib import Path
import sys

# Every other tool in this folder does this and this one did not, so it only
# ever ran with PYTHONPATH set by hand and failed with ModuleNotFoundError for
# anybody who ran it the way its own documentation describes.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from echo_link_app.main import diagnostics  # noqa: E402


def main() -> int:
    report = diagnostics()
    required = {
        "runtime": report.get("runtime") == "local-only",
        "speech_model": bool(report.get("speech_model_ready")),
        "hotkey": bool(report.get("hotkey_valid")) and bool(report.get("hotkey_registered")),
        "audio_retention": report.get("audio_retention") is False,
    }
    print(json.dumps({"passed": all(required.values()), "checks": required, "details": report}, indent=2))
    return 0 if all(required.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
