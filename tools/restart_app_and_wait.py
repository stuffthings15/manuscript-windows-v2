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


def stop_the_running_app() -> int:
    """Stop Manuscript whatever interpreter it is running under.

    This used to kill pythonw.exe by image name, which was right until the
    launcher stopped using pythonw. Windows Application Control blocks
    pythonw.exe on this machine, so run_dev_app falls back to python.exe -
    and the killer did not follow it.

    Measured on 2026-08-20: the app had been running as python.exe since
    10:47, roughly six hours, while every "restart" in between reported
    success and changed nothing. Every Word measurement in that window ran
    against code from 10:47, so four separate product fixes were invisible to
    the harness that was supposed to prove them, and the HUD kept reporting
    behaviour those fixes had already changed.

    Matched on the command line, not the image name, and only ever on
    run_echo_link. Killing python.exe by image would take the test suite, the
    grader and this tool's own parent with it.

    Returns how many processes were stopped, so a caller can tell "nothing was
    running" from "the old one is still there".
    """
    script = "run_echo_link"
    query = (
        "Get-CimInstance Win32_Process | "
        f"Where-Object {{ $_.CommandLine -like '*{script}*' }} | "
        "ForEach-Object { $_.ProcessId }"
    )
    try:
        found = subprocess.run(  # noqa: S603
            ["powershell", "-NoProfile", "-Command", query],
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    stopped = 0
    for line in found.splitlines():
        pid = line.strip()
        if not pid.isdigit():
            continue
        subprocess.call(  # noqa: S603
            ["taskkill", "/F", "/PID", pid],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        stopped += 1
    print(f"stopped {stopped} running app process(es)", flush=True)
    return stopped


def main() -> int:
    start = log_length()
    stop_the_running_app()
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
