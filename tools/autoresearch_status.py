"""What AutoResearch is doing right now, for anyone watching the screen.

Curtis asked to be able to see that AutoResearch is running, and for that to
appear ONLY while it is running. This is the state half; `autoresearch_monitor`
is the window that shows it.

Modelled on `echo_link_core/session_state.py`, which does the same job for the
HUD: an atomic write to one small JSON file, a tolerant read, and no shared
memory between the writer and the reader. That shape is deliberate and its
reasons are recorded there - a reader polling a few times a second WILL catch a
partial write otherwise, and a harness that reads half a JSON document is worse
than one that reads none, because it makes a decision from it.

Living in `tools/` rather than `echo_link_core/` because nothing here ships. The
app never reads or writes this file; only the runner and the monitor do, so
putting it in the product package would ship a research feature to customers.

The file is DELETED when a run finishes. That is the whole mechanism behind
"should not show up unless AutoResearch is being run": no file, no window.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import threading

_LOCK = threading.Lock()

# Beside the other harness state, so everything a run produces sits together and
# nothing lands in the user's documents.
_DIRECTORY = Path.home() / "AppData/Local/ManuscriptWindowsV2"


def status_path() -> Path:
    override = os.environ.get("MANUSCRIPT_STATE_DIR", "").strip()
    directory = Path(override) if override else _DIRECTORY
    return directory / "autoresearch-status.json"


_MONITOR: "subprocess.Popen | None" = None


def start_monitor() -> None:
    """Show the on-screen panel, if this process has not already started one.

    THE STATUS FILE IS NOT THE PANEL. Writing the file only records what is
    happening; a separate process, `autoresearch_monitor.py`, is what puts the
    green text on screen. This launcher used to live in `autoresearch_run`
    alone, so anything that measured WITHOUT going through the essay runner
    wrote a perfectly good status file that nobody rendered.

    Curtis, 2026-09-02, twice: "The auto research message in the top right-hand
    corner in green is not available." The first time the cause was baselines
    calling `measure()` directly and never writing the file at all. The second
    time the file was being written correctly and there was still nothing to see,
    because no viewer had been started. Both were the same mistake in different
    places: treating the panel as a side effect of one command rather than as
    part of what a measurement IS.

    pythonw, not python, so no console window appears and nothing flashes into
    the foreground. The panel itself refuses focus three different ways - see
    autoresearch_monitor - because a window that steals focus mid-experiment
    corrupts the very run it is reporting on.

    A module-level handle answers the only question worth asking: has THIS
    process already started one. An "is a status file present" guard was tried
    and was wrong twice over - the file must exist before the panel starts, so
    the test was always true by the time it ran, and it could not tell a live
    panel from one left behind by a killed run.
    """
    global _MONITOR
    if _MONITOR is not None and _MONITOR.poll() is None:
        return
    root = Path(__file__).resolve().parents[1]
    launcher = Path(sys.executable).with_name("pythonw.exe")
    try:
        _MONITOR = subprocess.Popen(  # noqa: S603 - a fixed path in this repository
            [
                str(launcher if launcher.exists() else sys.executable),
                str(root / "tools" / "autoresearch_monitor.py"),
            ],
            cwd=root,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
    except OSError:
        # The display is a convenience. Never lose an experiment over it.
        pass


def write_status(values: dict[str, object]) -> None:
    """Replace the status file atomically, or do nothing it cannot recover from.

    A failure here must never take down an experiment. The status display is a
    convenience; the measurement is the point, and losing a twelve minute Word
    run because a JSON file could not be written would be an absurd trade.
    """
    payload = dict(values)
    payload.setdefault("stamp", datetime.now().isoformat(timespec="seconds"))
    target = status_path()
    try:
        with _LOCK:
            target.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(
                dir=str(target.parent), suffix=".tmp"
            )
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2)
            os.replace(temporary, target)
    except OSError:
        # Deliberately silent. See the docstring: the run matters, this does not.
        pass


class showing:
    """Publish the panel for the whole of a measurement, whatever happens.

    THE PANEL IS THE PROMISE THAT A RUN IS REAL. Curtis, 2026-09-02: "The auto
    research message in the top right-hand corner in green is not available. It
    is not showing, and it should be showing if we are conducting auto research."
    He was right, and the cause was a process failure rather than a bug: only the
    cycles' `main()` published the panel, so every baseline measured that night
    went through a throwaway script that called `measure()` directly and showed
    nothing. Those scripts also lived outside the repository, so nothing measured
    that way was reproducible by anyone else.

    Binding the panel to the MEASUREMENT rather than to a command-line wrapper
    makes the failure impossible to repeat: any caller that measures, shows.

    Never raises, and always clears. A panel left up after a crash is a lie
    about what the machine is doing, which is the whole reason `main` already
    took the panel down in a `finally`.
    """

    def __init__(self, **values: object) -> None:
        self._values = values

    def __enter__(self) -> "showing":
        # File first, then the viewer: the panel quits after four missing
        # reads, so starting it before there is anything to read races it.
        write_status(self._values)
        start_monitor()
        return self

    def __exit__(self, *_exception: object) -> bool:
        clear_status()
        return False


def read_status() -> dict | None:
    """The current status, or None when nothing is running.

    None covers both "no file" and "a file that cannot be parsed", because a
    half-written document and an absent one mean the same thing to a viewer:
    nothing trustworthy to show.
    """
    try:
        return json.loads(status_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def clear_status() -> None:
    """Remove the file, which is what makes the display disappear."""
    try:
        status_path().unlink()
    except OSError:
        pass
