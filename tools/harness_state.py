"""Read what the HUD says it is doing, instead of guessing from its label.

The harness used to decide the product's state by string-matching English prose
out of an accessibility name, and anything the sentence did not literally say
became "unknown". A failing tone run on 2026-08-21 reported `unknown=54`: it
inspected the card fifty-four times, never recognised the state, never pressed
Replace, and reported `applied=0` - which reads exactly like a product that
corrected nothing.

The event log from that same run showed the product had done its part:

    tone_requested    Friendly members=2 characters=956
    tone_batch_filed  members=2
    card_restored

and then the harness sat for thirty seconds and gave up. The signal existed;
nothing was reading it.

The app now publishes `hud-state.json`. This reads it, and falls back to the old
label-scraping when the file is absent so an older build still runs.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

# How old a reading may be before it is treated as no reading at all. The HUD
# rewrites its state on every change, so a file older than this means the app
# has stopped updating - which is itself the answer to "why is nothing
# happening".
STALE_SECONDS = 30.0


def state_directory() -> Path:
    override = os.environ.get("MANUSCRIPT_STATE_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / "AppData" / "Local" / "ManuscriptWindowsV2"


def read_state() -> dict | None:
    """The current HUD state, or None when there is nothing trustworthy.

    None means "fall back to the label", never "the HUD is idle". Those are
    different facts and conflating them is how the harness got here.
    """
    path = state_directory() / "hud-state.json"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        state = json.loads(raw)
    except ValueError:
        # A partial read. The writer replaces the file atomically, so this
        # should not happen; if it does, the next poll will be whole.
        return None
    if not isinstance(state, dict):
        return None
    return state


def recent_state(now: float | None = None) -> dict | None:
    """The state, only if the app has updated it recently enough to believe."""
    state = read_state()
    if state is None:
        return None
    path = state_directory() / "hud-state.json"
    try:
        age = (now or time.time()) - path.stat().st_mtime
    except OSError:
        return None
    return state if age <= STALE_SECONDS else None


def readiness(state: dict | None) -> str:
    """"ready", "finished", "preparing", or "" when the state cannot say.

    Returns the same vocabulary the label-scraping version returned, so callers
    do not have to know which source answered - but it can no longer return
    "unknown", because a structured state either says or is absent.
    """
    if not state:
        return ""
    total = int(state.get("sections_total") or 0)
    if not total:
        return ""
    ready = int(state.get("sections_ready") or 0)
    refused = int(state.get("sections_refused") or 0)
    if ready + refused < total:
        return "preparing"
    return "finished" if refused else "ready"


def can_press_replace(state: dict | None) -> bool:
    """Whether the card in front of the writer can be applied right now."""
    if not state:
        return False
    return bool(state.get("replace_enabled")) and state.get("view") == "card"


def selected_tone(state: dict | None) -> str:
    return str((state or {}).get("tone_selected") or "")


def build(state: dict | None) -> str:
    return str((state or {}).get("build") or "")


def describe(state: dict | None) -> str:
    """One line a person can act on, for a timeout message.

    Every diagnosis this week came from someone reading a live dump by hand.
    A harness that gives up should say what it saw, in the same breath.
    """
    if not state:
        return "no HUD state was published (older build, or the app is not running)"
    return (
        f"section {state.get('section_index')}/{state.get('section_total')}, "
        f"ready {state.get('sections_ready')}, "
        f"refused {state.get('sections_refused')}, "
        f"view={state.get('view')}, "
        f"replace_enabled={state.get('replace_enabled')}, "
        f"tone={state.get('tone_selected')}, "
        f"provenance={state.get('provenance')}, "
        f"as of {str(state.get('stamp'))[11:19]}"
    )


def recent_events(limit: int = 12) -> list[dict]:
    """The last few transitions, for a timeout message."""
    path = state_directory() / "hud-events.jsonl"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    found: list[dict] = []
    for line in lines[-limit:]:
        try:
            found.append(json.loads(line))
        except ValueError:
            continue
    return found


def wait_for(predicate, timeout: float, what: str, poll: float = 0.5) -> dict | None:
    """Wait for a condition on the published state. Returns the state, or None.

    `what` is a description of the thing being waited for, and it exists so a
    timeout can say what it wanted rather than only that it waited.
    """
    deadline = time.monotonic() + timeout
    state = None
    while time.monotonic() < deadline:
        state = recent_state()
        if state is not None and predicate(state):
            return state
        time.sleep(poll)
    return None


def timeout_report(what: str, waited: float) -> str:
    """What to print when a wait gives up, with the evidence attached."""
    state = read_state()
    lines = [
        f"TIMED OUT after {waited:.0f}s waiting for: {what}",
        f"  HUD said: {describe(state)}",
    ]
    events = recent_events()
    if events:
        lines.append("  last transitions:")
        for entry in events:
            extra = {k: v for k, v in entry.items() if k not in {"stamp", "event"}}
            lines.append(f"    {str(entry.get('stamp'))[11:19]} {entry.get('event')} {extra}")
    return "\n".join(lines)
