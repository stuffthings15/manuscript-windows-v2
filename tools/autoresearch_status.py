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
