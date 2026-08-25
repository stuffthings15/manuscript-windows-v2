"""The signal the harness was missing.

Measured 2026-08-21: a failing tone run reported `unknown=54` - the harness had
inspected the card fifty-four times and could not tell what state it was in,
because state was only ever published as an English sentence in an
accessibility label. It pressed nothing and reported `applied=0`, which reads
exactly like a product that corrected nothing.
"""
from __future__ import annotations

import json

import pytest

from echo_link_core import session_state


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MANUSCRIPT_STATE_DIR", str(tmp_path / "state"))


def test_state_is_readable_after_every_write(tmp_path):
    session_state.write_state(
        {
            "view": "card",
            "section_index": 1,
            "section_total": 25,
            "sections_ready": 21,
            "replace_enabled": True,
            "tone_selected": "Friendly",
        }
    )

    state = json.loads(session_state.state_path().read_text(encoding="utf-8"))
    assert state["section_index"] == 1
    assert state["sections_ready"] == 21
    assert state["replace_enabled"] is True
    assert state["tone_selected"] == "Friendly"
    assert state["stamp"], "a state with no timestamp cannot be judged stale"


def test_a_reader_never_sees_a_half_written_state():
    """A harness polling several times a second WILL catch a partial write.

    Reading half a JSON document is worse than reading none, because the
    harness makes a decision from it. The write goes to a temporary file and is
    moved into place, so the state file is only ever whole.
    """
    for index in range(40):
        session_state.write_state({"section_index": index, "section_total": 25})
        # Whatever a reader catches between writes must parse.
        body = session_state.state_path().read_text(encoding="utf-8")
        assert json.loads(body)["section_index"] == index
    assert not list(session_state.state_directory().glob("*.tmp")), "temp file left behind"


def test_events_record_the_order_things_happened():
    """A snapshot cannot show that a review restarted nine times."""
    session_state.record_event("review_prepared", sections=25)
    session_state.record_event("tone_requested", tone="Friendly", section=1)
    session_state.record_event("review_reset", reason="recapture")

    lines = [
        json.loads(line)
        for line in session_state.events_path().read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [entry["event"] for entry in lines] == [
        "review_prepared",
        "tone_requested",
        "review_reset",
    ]
    assert lines[1]["tone"] == "Friendly"


def test_the_event_log_does_not_grow_without_bound(monkeypatch):
    monkeypatch.setattr(session_state, "_MAX_EVENT_BYTES", 2_000)
    for index in range(400):
        session_state.record_event("section_ready", section=index)

    size = session_state.events_path().stat().st_size
    assert size < 20_000, size
    # The RECENT half is what a stall is diagnosed from, so the last event must
    # survive the trim.
    tail = session_state.events_path().read_text(encoding="utf-8").splitlines()[-1]
    assert json.loads(tail)["section"] == 399


def test_neither_file_ever_carries_the_writers_text():
    """These files are read by tooling and pasted into reports."""
    session_state.write_state({"section_index": 1, "document": "essay.docx"})
    session_state.record_event("replaced", section=1)

    written = session_state.state_path().read_text(encoding="utf-8")
    written += session_state.events_path().read_text(encoding="utf-8")
    for field in ("original", "replacement", "text", "suggestion_text"):
        assert field not in written, f"{field} would carry the writer's words"


def test_a_state_directory_that_cannot_be_written_does_not_crash_the_app(monkeypatch):
    """Losing the signal must never take the product down with it."""
    monkeypatch.setenv("MANUSCRIPT_STATE_DIR", "\\\\?\\nope::invalid")

    session_state.write_state({"section_index": 1})
    session_state.record_event("advanced", section=1)


def test_the_build_is_published_so_a_run_cannot_measure_the_wrong_one(monkeypatch):
    """Three times in two days a measurement ran against a stale process."""
    monkeypatch.setenv("MANUSCRIPT_BUILD", "abc123def456")

    assert session_state.build_identifier() == "abc123def456"


def test_the_build_identifier_describes_the_running_process_not_the_checkout(
    monkeypatch, tmp_path
):
    """A staleness check that cannot detect staleness is worse than none.

    build_identifier read .git/HEAD on every call until 2026-08-25, so it
    always returned the CHECKOUT's head rather than the running process's. The
    harness compared HEAD against HEAD, always matched, and reported it as a
    passed check.

    Measured that night: an app process started on 2026-08-23 served six
    acceptance tiers while reporting its build as a commit made minutes
    earlier. Every number came from two-day-old code, and two of them were
    quoted as evidence about a model that code had never heard of.

    This pins the property that matters: once a process has started, moving
    the checkout must NOT change what that process reports.
    """
    from echo_link_core import session_state

    monkeypatch.delenv("MANUSCRIPT_BUILD", raising=False)
    started_as = session_state.build_identifier()

    # The checkout moves on underneath a long-running process.
    monkeypatch.setattr(session_state, "_read_head", lambda: "0000newhead0")

    assert session_state.build_identifier() == started_as, (
        "build_identifier followed the checkout instead of describing the "
        "running process"
    )


def test_an_explicit_build_override_still_wins():
    """Packaged builds have no .git, so the override has to keep working."""
    import os

    from echo_link_core import session_state

    os.environ["MANUSCRIPT_BUILD"] = "packaged12345"
    try:
        assert session_state.build_identifier() == "packaged12345"
    finally:
        del os.environ["MANUSCRIPT_BUILD"]
