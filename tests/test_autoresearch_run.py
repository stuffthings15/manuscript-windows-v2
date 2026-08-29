"""The AutoResearch runner must refuse to score what it cannot vouch for.

Every test here exists because of a specific way this project has previously
produced a number that meant nothing.

The runner is the evaluator, the equivalent of upstream's read-only
`prepare.py`, so it is the one part of the loop the loop may not edit. That
makes these tests the thing standing between an overnight run and a convincing
results.tsv full of noise.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import autoresearch_run  # noqa: E402


def test_a_stale_app_produces_no_score(monkeypatch, capsys):
    """The defect that would have wasted the entire first night.

    The app is a long-running process. An experiment that edits polish*.py and
    measures the already-loaded code returns the baseline plus noise, so every
    keep and discard would be decided by chance while the log looked entirely
    reasonable. session_state.build_identifier records the same failure three
    times in two days, one of which cost six hours.
    """
    monkeypatch.setattr(
        autoresearch_run, "_stale_reason", lambda: "the running app is build aaa"
    )

    # A stale run must not even START the Word batch. Asserting only on the
    # output would let this test "pass" by hanging for five minutes on a real
    # correction run, which is exactly what it did when the guard was first
    # disabled to check it was load-bearing.
    real_run = autoresearch_run.subprocess.run

    def _no_batch(command, *args, **kwargs):
        # `git rev-parse` runs before the staleness check to stamp the build,
        # and that is fine. What must never happen is the five minute Word run.
        if any("run_essays_batch" in str(part) for part in command):
            raise AssertionError("a stale app must not launch a Word run at all")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(autoresearch_run.subprocess, "run", _no_batch)

    exit_code = autoresearch_run.main(["--no-restart"])
    printed = capsys.readouterr().out

    assert exit_code == 1
    assert "crash:" in printed
    # The loop greps for score: and treats its absence as a crash. A stale run
    # that emitted one would be indistinguishable from a real measurement.
    assert "score:" not in printed


def test_a_staleness_check_that_cannot_run_is_a_failure(monkeypatch):
    """A check that errors must not read as a pass.

    That is the exact shape of the bug it exists to catch: the staleness
    comparison was inert for weeks because it compared HEAD against HEAD, and
    reported a pass every time.
    """
    def _explode():
        raise RuntimeError("harness import blew up")

    monkeypatch.setattr(autoresearch_run, "_stale_app_reason_source", _explode, raising=False)
    monkeypatch.setitem(sys.modules, "release_acceptance", None)

    reason = autoresearch_run._stale_reason()

    assert reason, "a staleness check that cannot run must report a reason"


def test_a_partial_or_dead_run_is_a_crash_not_a_low_score():
    """Scoring these is how a mean of 84.5 was once reported as a regression.

    Eleven of twenty runs had stopped part way and were averaged in as though
    they were results. A partial essay carries a real-looking number, which is
    what makes it more dangerous than an obvious failure.
    """
    assert autoresearch_run._row_status(" 3 e run1 210.2s dead    presses= 0") == "dead"
    assert autoresearch_run._row_status(" 9 e run1 247.4s partial presses=15") == "partial"
    assert autoresearch_run._row_status(" 1 e run1 311.0s complete presses=25") == "complete"


def test_a_review_captured_from_another_window_is_a_crash():
    """The HUD follows focus by design, so this is a routine failure here.

    A review built from 26 characters of chat text was once clicked five times
    and reported as "sections=5 accepted=5" while the essay came back
    byte-identical.
    """
    row = " 4 e run1 11.3s error err=the review on screen was captured from 'Claude'"

    assert autoresearch_run._row_status(row) == "capture-mismatch"


def test_the_graded_slice_comes_from_the_rows_that_ran():
    """build/batch accumulates results from every earlier run.

    Grading it wholesale on 2026-08-27 mixed three corpora and several failed
    runs into a mean of 96.0 that belonged to nothing. Names are truncated to
    thirty characters in the rows, so the caller matches by prefix.
    """
    rows = [
        " 1 01_urban_beekeeping_and_the_ec run1  311.0s complete",
        " 2 02_how_public_libraries_streng run1  322.8s complete",
    ]

    names = autoresearch_run._slice_names(None, rows)

    assert names == [
        "01_urban_beekeeping_and_the_ec",
        "02_how_public_libraries_streng",
    ]


def test_vram_is_reported_as_zero_rather_than_crashing_without_a_gpu(monkeypatch):
    """A missing GPU is not a reason to lose an otherwise good experiment."""
    monkeypatch.setattr(autoresearch_run, "_nvidia_status", None, raising=False)

    assert autoresearch_run._vram_used_mb() >= 0.0


def test_the_status_panel_appears_only_while_a_run_is_happening(monkeypatch, tmp_path):
    """Curtis asked to see the loop working, and to see nothing when it is not.

    The panel lives exactly as long as the status file does, so every exit from
    a run must remove it. There are half a dozen ways out - a stale app, a
    crash, a timeout, a pack mismatch - and a panel left on screen after any of
    them would misreport what the machine is doing.
    """
    import autoresearch_status

    monkeypatch.setenv("MANUSCRIPT_STATE_DIR", str(tmp_path))
    autoresearch_status.write_status({"phase": "correcting essay 8"})
    assert autoresearch_status.read_status() is not None

    # A run that dies on the staleness guard still takes the panel down.
    monkeypatch.setattr(autoresearch_run, "_stale_reason", lambda: "stale")
    monkeypatch.setattr(autoresearch_run, "_start_monitor", lambda: None)

    autoresearch_run.main(["--no-restart"])

    assert autoresearch_status.read_status() is None


# The idle-gate tests were removed on 2026-08-29, not weakened.
#
# They asserted that an experiment stands down while somebody is at the
# keyboard, which was real behaviour until Curtis decided to run continuously as
# upstream does. The gate had become a trap rather than a protection: he is at
# the keyboard when he prompts, so it blocked the next experiment, so he saw
# nothing happen, so he prompted again.
#
# A test for a deliberately removed feature is not a safety net, it is a record
# of a decision - and this comment is the better place for that record.
