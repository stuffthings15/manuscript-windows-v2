"""Restart Manuscript from source and wait until its model is really loaded.

Every live Word run here that produced a misleading result did so because the
app under test was not the app the code had been changed in, or because it had
come up rules-only and nothing said so. This does the restart and then blocks
until the app's own log says the model loaded, so a run either starts against a
working app or does not start.

    .venv/Scripts/python.exe tools/restart_app_and_wait.py
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = Path.home() / "AppData/Local/ManuscriptWindowsV2/echo-link.log"


def log_length() -> int:
    try:
        return len(
            LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
        )
    except OSError:
        return 0


def main() -> int:
    start = log_length()
    # pythonw has no console, so the old instance is stopped by image name.
    for image in ("pythonw.exe",):
        subprocess.call(
            ["taskkill", "/F", "/IM", image],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    time.sleep(2.0)
    subprocess.Popen(
        [
            str(ROOT / ".venv" / "Scripts" / "python.exe"),
            str(ROOT / "tools" / "run_dev_app.py"),
        ],
        cwd=str(ROOT),
    )
    print("launched; waiting for the model to load", flush=True)

    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        try:
            lines = LOG_PATH.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()[start:]
        except OSError:
            lines = []
        for line in lines:
            if "Adaptive Polish model prewarmed" in line:
                loaded = "loaded=True" in line
                print(("ready: " if loaded else "NOT ready: ") + line[-120:])
                return 0 if loaded else 4
            if "Adaptive Polish came up rules-only" in line:
                print("NOT ready: " + line[-120:])
                return 4
        time.sleep(3.0)
    print("timed out waiting for the app to report a loaded model")
    return 5


if __name__ == "__main__":
    raise SystemExit(main())
