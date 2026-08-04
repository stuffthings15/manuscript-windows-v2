from __future__ import annotations

import json

from echo_link_app.main import diagnostics


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
