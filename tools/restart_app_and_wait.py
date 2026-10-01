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
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.application_log_cursor import capture_cursor, lines_since  # noqa: E402

LOG_PATH = Path.home() / "AppData/Local/ManuscriptWindowsV2/echo-link.log"



# Must match whatever tools/run_dev_app.py launches. It was "run_echo_link"
# until 2026-09-30; a matcher that does not follow the launcher cannot find
# the running app, so stop_the_running_app reports nothing stopped, the new
# instance hits the single-instance mutex, exits silently, and the wait times
# out. That is indistinguishable from a broken app.
APP_SCRIPT = "run_manuscript"


def app_process_ids(script: str = APP_SCRIPT) -> list[str]:
    """Process ids running Manuscript, matched on the command line.

    Matched on the command line and never on the image name, for the reason
    `stop_the_running_app` records below: the launcher moved from pythonw.exe
    to python.exe and a killer matching the image silently stopped doing
    anything for six hours.
    """
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
        return []
    return [line.strip() for line in found.splitlines() if line.strip().isdigit()]


def app_is_running() -> bool:
    """Is Manuscript alive right now?

    THE DIRECT QUESTION, asked because every proxy for it has been wrong. The
    harness waited a fixed 10 seconds for the app to capture a document, then
    40, then 120 - and then on whether the app's log was still growing, which
    sounded principled and was not. Measured 2026-09-08 over 14 app starts, the
    log goes SILENT for a median of 14.6 seconds while the model loads, with a
    90th percentile of 122.8 and a maximum of 131.7. A 30 second quiet window
    survives 64 percent of starts, which is what turned 9 of 20 essays into
    "the app reported no capture at all" in a run where the app was working
    perfectly well.

    A loading app is silent. A dead one is also silent. Only this tells them
    apart.
    """
    return bool(app_process_ids())


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
    run_manuscript. Killing python.exe by image would take the test suite, the
    grader and this tool's own parent with it.

    Returns how many processes were stopped, so a caller can tell "nothing was
    running" from "the old one is still there".
    """
    script = APP_SCRIPT
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
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.touch(exist_ok=True)
    start = capture_cursor(LOG_PATH)
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
            lines = lines_since(LOG_PATH, start)
        except (OSError, RuntimeError) as exc:
            print(f"startup evidence unavailable: {exc}")
            return 6
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
