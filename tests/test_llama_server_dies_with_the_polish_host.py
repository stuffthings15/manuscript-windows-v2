"""llama-server dies with the polish host, however the host dies.

Found 2026-10-01. Force-stopping Manuscript.PolishHost.exe left its
llama-server.exe child running and holding the model in VRAM. Three orphans
piled up, one at about 14 GB, and the next two app starts came up rules-only
because the fallback model could only be partly GPU-resident ("1.45 GB of
3.12 GB is resident"). The host already shuts its child down when the APP
exits, through its parent monitor; nothing covered the host itself being
killed, which is what a crash or Task Manager does.

The host now places its child in a job object created with
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, held open for the host's lifetime, so
Windows ends the child when the host's handles close for any reason.

This runs the real executable: "--self-test-child-job" starts a stand-in
child through the same code path as llama-server and reports its PID. The
host is then killed with taskkill /F and WITHOUT /T, which never touches the
child itself; only the job can end it.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "resources" / "polish-host" / "Manuscript.PolishHost.exe"


def _alive(pid: int) -> bool:
    listing = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout
    return f'"{pid}"' in listing


def test_the_child_dies_when_the_host_is_force_killed():
    if not HOST.is_file():
        pytest.skip("the polish host has not been built into resources/")
    host = subprocess.Popen(
        [str(HOST), "--self-test-child-job"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    child = 0
    try:
        line = host.stdout.readline().strip() if host.stdout else ""
        assert line.startswith("child-pid "), (
            line or (host.stderr.read() if host.stderr else "")
        )
        child = int(line.split()[1])
        assert _alive(child), "the stand-in child did not start"

        subprocess.run(
            ["taskkill", "/PID", str(host.pid), "/F"],
            capture_output=True,
            timeout=30,
        )
        host.wait(timeout=30)

        deadline = time.monotonic() + 15
        while _alive(child) and time.monotonic() < deadline:
            time.sleep(0.5)
        assert not _alive(child), "the child outlived a force-killed host"
    finally:
        if host.poll() is None:
            host.kill()
        if child and _alive(child):
            # Never leave the stand-in behind, whatever the assertion said.
            subprocess.run(
                ["taskkill", "/PID", str(child), "/F", "/T"],
                capture_output=True,
                timeout=30,
            )
