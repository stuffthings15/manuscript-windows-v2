"""A review that finished must not be scored as a review that vanished.

Measured 2026-09-02 on 03_multi_level_outline. The app logged:

    section accepted ... pending 1 -> 0 dropped=0 completed=4 of 4
    Writing HUD review finished; will not auto-offer this text again chars=263

Every section was applied and the HUD closed because it was DONE. The harness
pressed once more, correctly found no HUD, ran recovery three times and reported
"the HUD was gone at press 5" - and because that message carries no `section
M/M` marker, `_run_was_truncated` scored a successful run as truncated.

Two consequences, both wrong. The reliability metric under-reported, sitting at
80.0 when the run had completed. And three runs of "the HUD was gone" were read
as a focus fault that never happened: the foreground at every failed recovery
attempt was Word itself, with the correct document open.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT / "tools", ROOT / "src"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import release_acceptance  # noqa: E402
import run_essays_batch  # noqa: E402

FINISHED = (
    "2026-09-02 20:14:26,616 INFO echo_link_app.window: Writing HUD review "
    "finished; will not auto-offer this text again chars=263 remembered=3"
)


def test_the_app_statement_is_read_back(tmp_path, monkeypatch):
    log = tmp_path / "echo-link.log"
    log.write_text("earlier line\n" + FINISHED + "\n", encoding="utf-8")
    monkeypatch.setattr(run_essays_batch, "LOG", log)

    assert run_essays_batch.review_finished_since(0) == "chars=263 remembered=3"


def test_a_finish_before_the_mark_does_not_count(tmp_path, monkeypatch):
    """The log is append-only across essays; the previous one must not count."""
    log = tmp_path / "echo-link.log"
    log.write_text(FINISHED + "\nthis essay started here\n", encoding="utf-8")
    monkeypatch.setattr(run_essays_batch, "LOG", log)

    assert run_essays_batch.review_finished_since(1) == ""


def test_no_statement_means_no_finish(tmp_path, monkeypatch):
    log = tmp_path / "echo-link.log"
    log.write_text("the HUD did something else\n", encoding="utf-8")
    monkeypatch.setattr(run_essays_batch, "LOG", log)

    assert run_essays_batch.review_finished_since(0) == ""


def test_a_finished_review_scores_complete():
    row = {
        "presses": 4,
        "stalled_at": None,
        "error": None,
        "stopped_because": (
            "the review finished after press 4; section 4/4, chars=263 remembered=3"
        ),
    }
    assert release_acceptance._run_was_truncated(row) is False


def test_a_genuinely_vanished_hud_still_scores_truncated():
    """The rule must not be loosened into uselessness.

    A HUD that disappeared with sections outstanding is a real failure and has
    to stay one.
    """
    row = {
        "presses": 4,
        "stalled_at": None,
        "error": None,
        "stopped_because": (
            "the HUD was gone at press 5 and three attempts to take focus back "
            "from another window failed"
        ),
    }
    assert release_acceptance._run_was_truncated(row) is True


def test_final_accept_reads_completion_before_trying_to_recover_a_closed_hud(tmp_path, monkeypatch):
    log = tmp_path / "echo-link.log"
    log.write_text("start\n" + FINISHED + "\n", encoding="utf-8")
    monkeypatch.setattr(run_essays_batch, "LOG", log)
    report = {"_log_start": 1, "presses": 7}
    assert run_essays_batch.record_review_completion(report, 7)
    assert report["stopped_because"].startswith("the review finished after press 7")
    assert report["completion_acknowledgement"] == "chars=263 remembered=3"


def test_previous_document_completion_cannot_acknowledge_this_one(tmp_path, monkeypatch):
    log = tmp_path / "echo-link.log"
    log.write_text(FINISHED + "\nstart\n", encoding="utf-8")
    monkeypatch.setattr(run_essays_batch, "LOG", log)
    report = {"_log_start": 1}
    assert not run_essays_batch.record_review_completion(report, 7)
    assert "stopped_because" not in report


def test_repeated_review_still_emits_completion(caplog):
    import logging
    from types import SimpleNamespace
    from echo_link_app.window import EchoLinkWindow
    stub = SimpleNamespace(selected_text_record=SimpleNamespace(text="Already reviewed."),
                           _reviewed_texts=["Already reviewed."])
    with caplog.at_level(logging.INFO, logger="echo_link_app.window"):
        EchoLinkWindow._remember_reviewed_text(stub)
    assert "Writing HUD review finished;" in caplog.text
    assert stub._reviewed_texts == ["Already reviewed."]


def test_completion_and_commands_survive_application_log_rotation(tmp_path, monkeypatch):
    import run_essays_batch as batch
    log = tmp_path / "app.log"
    monkeypatch.setattr(batch, "LOG", log)
    log.write_text("old review finished; will not auto-offer this text again stale\n")
    mark = batch.log_lines()
    with log.open("a") as stream:
        stream.write("Polish request command=Clear characters=12\n")
    log.rename(tmp_path / "app.log.1")
    log.write_text("review finished; will not auto-offer this text again chars=12\n")
    assert batch.commands_since(mark) == {"Clear": 1}
    assert batch.review_finished_since(mark) == "chars=12"


def test_lost_log_cursor_is_an_invalid_measurement(tmp_path, monkeypatch):
    import pytest
    import run_essays_batch as batch
    log = tmp_path / "app.log"
    monkeypatch.setattr(batch, "LOG", log)
    log.write_text("original\n")
    mark = batch.log_lines()
    log.rename(tmp_path / "outside-retention.log")
    log.write_text("review finished; will not auto-offer this text again unrelated\n")
    with pytest.raises(RuntimeError, match="cursor lost"):
        batch.review_finished_since(mark)


def test_capture_wait_does_not_accept_the_previous_document(monkeypatch):
    import run_essays_batch as batch
    values = iter(["old document", "old document", "unique-target - Word"])
    monkeypatch.setattr(batch, "captured_document_title", lambda _: next(values))
    monkeypatch.setattr(batch.time, "sleep", lambda _: None)
    assert batch.wait_for_document_capture("unique-target", 0) == "unique-target - Word"


def test_capture_wait_has_a_bound_and_does_not_invent_identity(monkeypatch):
    import run_essays_batch as batch
    clock = iter([0.0, 0.1, 0.3])
    monkeypatch.setattr(batch.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(batch.time, "sleep", lambda _: None)
    monkeypatch.setattr(batch, "captured_document_title", lambda _: None)
    assert batch.wait_for_document_capture("target", 0, timeout=0.2) is None
