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
