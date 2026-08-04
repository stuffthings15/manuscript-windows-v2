"""Run the full-page journey probe only once the desktop is untouched.

Focus-dependent UI Automation cannot share the desktop with a human: every
real mouse click or keystroke steals foreground focus and breaks capture.
This wrapper waits for the machine to be input-idle, then runs the probe.
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IDLE_REQUIRED_SECONDS = 90.0
MAX_WAIT_SECONDS = 45 * 60


class _LastInputInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def _idle_seconds() -> float:
    info = _LastInputInfo()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    return max(0.0, (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000.0)


def main() -> int:
    waited = 0.0
    while _idle_seconds() < IDLE_REQUIRED_SECONDS:
        if waited >= MAX_WAIT_SECONDS:
            print("Gave up: the desktop never went idle.")
            return 3
        time.sleep(15)
        waited += 15
    # A leftover Notepad turns every later file into a tab of its window,
    # which breaks capture. Start each run from a clean slate.
    subprocess.call(
        ["taskkill", "/F", "/IM", "notepad.exe"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1.0)
    print(f"Desktop idle for {IDLE_REQUIRED_SECONDS:.0f}s; starting the probe.")
    return subprocess.call(
        [
            sys.executable,
            str(ROOT / "tools" / "full_page_user_journey_probe.py"),
            *sys.argv[1:],
        ],
        cwd=ROOT,
    )


if __name__ == "__main__":
    raise SystemExit(main())
