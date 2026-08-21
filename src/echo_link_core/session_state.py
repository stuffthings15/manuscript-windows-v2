"""What the writing HUD is doing right now, in a form a machine can read.

The measurement harness decided what the product was doing by string-matching
English prose out of an accessibility label:

    if "all sections ready" in name:    return "ready"
    if "preparing" in name:             return "preparing"
    return "unknown"

Anything the label did not literally say became "unknown", and a failing run
reported `unknown=54` - it had looked at the card fifty-four times and could
not tell what it was looking at. It then pressed nothing and reported
`applied=0`, which is indistinguishable from a product that corrected nothing.

The app knew the answer the whole time. `rewrite_palette` computes the section
total, the ready count and the refused count in order to build that very label,
and throws the numbers away into a string. This writes them down instead.

Two files, in the app's own data directory:

    hud-state.json    what is true NOW, rewritten on every change
    hud-events.jsonl  what HAPPENED, appended, one line per transition

The state file answers "can I press Replace yet". The event log answers "why
did this review restart nine times in twenty minutes", which took a person
watching a live window to discover.

NEVER the writer's text. Counts, indexes, ids and outcomes only - the same rule
the dictation instrumentation follows. These files are read by tooling and
pasted into reports.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
# Enough history to explain a stall without growing without bound. A stalled
# run produces a handful of transitions, not thousands.
_MAX_EVENT_BYTES = 2 * 1024 * 1024


def state_directory() -> Path:
    """Where the state files live, overridable so a test never writes here."""
    override = os.environ.get("MANUSCRIPT_STATE_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / "AppData" / "Local" / "ManuscriptWindowsV2"


def state_path() -> Path:
    return state_directory() / "hud-state.json"


def events_path() -> Path:
    return state_directory() / "hud-events.jsonl"


def write_state(values: dict[str, object]) -> None:
    """Replace the state file atomically.

    Written to a temporary file and moved into place, because a reader polling
    a few times a second WILL catch a partial write otherwise, and a harness
    that reads half a JSON document is worse than one that reads none: it makes
    a decision from it.
    """
    payload = dict(values)
    payload.setdefault("stamp", datetime.now().isoformat(timespec="milliseconds"))
    target = state_path()
    try:
        with _LOCK:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            # Windows denies a replace while anything holds the destination
            # open, including a harness polling it and the machine's own
            # antivirus. Measured while writing this: forty writes in a tight
            # loop hit it once. A dropped state update makes the harness act on
            # a stale reading, so it is worth a few short retries.
            for attempt in range(5):
                try:
                    os.replace(tmp, target)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.02)
    except OSError:
        # Losing the state file must never take the app down with it. The
        # harness falls back to reading the accessibility label, which is what
        # it did before this existed.
        logger.warning("Could not write the HUD state file", exc_info=True)


def record_event(event: str, **fields: object) -> None:
    """Append one transition to the event log.

    Appended rather than replaced, because the order of transitions is the
    whole value: "review_reset" nine times in twenty minutes is a diagnosis,
    and no snapshot of the current state can show it.
    """
    line = {
        "stamp": datetime.now().isoformat(timespec="milliseconds"),
        "event": str(event),
    }
    line.update({key: value for key, value in fields.items() if value is not None})
    target = events_path()
    try:
        with _LOCK:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.stat().st_size > _MAX_EVENT_BYTES:
                # Keep the recent half. A stall is diagnosed from the last few
                # minutes, and an unbounded file in the app's data directory is
                # its own defect.
                kept = target.read_text(encoding="utf-8", errors="replace").splitlines()
                target.write_text(
                    "\n".join(kept[len(kept) // 2 :]) + "\n", encoding="utf-8"
                )
            with target.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(line) + "\n")
    except OSError:
        logger.warning("Could not append to the HUD event log", exc_info=True)


def build_identifier() -> str:
    """Which build is running, so a measurement cannot be taken against another.

    Three separate times in two days a run measured a process that was not the
    build under test, and one of those cost six hours. The harness compares
    this against the checkout it is testing and refuses to start when they
    differ.
    """
    override = os.environ.get("MANUSCRIPT_BUILD", "").strip()
    if override:
        return override
    head = Path(__file__).resolve().parents[2] / ".git" / "HEAD"
    try:
        ref = head.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"
    if ref.startswith("ref: "):
        pointer = head.parent / ref[5:]
        try:
            return pointer.read_text(encoding="utf-8").strip()[:12]
        except OSError:
            return "unknown"
    return ref[:12]
