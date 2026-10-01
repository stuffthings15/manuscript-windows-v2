"""The panel must say what the loop is doing, and be right about it.

Curtis, 2026-09-04: "The auto research heads-up display is still displaying 0,
kept 0 discarded, and 0 crashed; it is not being updated with the results and
does not indicate that it is working correctly."

He was right three times over, and none of it was a display bug:

1. NOTHING had ever written kept, discarded or crashed. The monitor read
   status.get("kept", 0) and rendered its own default, so those counters had
   been decorative since the day the panel was built. A loop working perfectly
   and a loop stuck on entry one looked identical.
2. A run killed part way cannot clear its status file, so the panel went on
   announcing a phase that had finished hours earlier. That is worse than
   showing nothing: an empty screen says nothing, a stale panel claims work.
3. It reported "essays 1-20" while measuring a three-document slice, because
   the count came from the default corpus rather than the slice being run.

Counts come from results.tsv rather than from a variable, because the ledger is
the only record a person can check afterwards. A counter in memory and a row on
disk are two things that can disagree, and the panel would be the one nobody
could audit.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import autoresearch_status  # noqa: E402

HEADER = "commit\tval\tmem\tstatus\tdescription\n"
ROWS = [
    "aaa1111\t100.0\t0.0\tkeep\tessays 8: an idea that worked\n",
    "bbb2222\t94.0\t0.0\tdiscard\tessays 8: an idea that did not\n",
    "ccc3333\t0.000000\t0.0\tcrash\tessays 8: a run that died\n",
    "ddd4444\t96.0\t0.0\tdiscard\tessays 8: another that did not\n",
]


def test_the_tally_counts_every_outcome(tmp_path):
    ledger = tmp_path / "results.tsv"
    ledger.write_text(HEADER + "".join(ROWS), encoding="utf-8")
    assert autoresearch_status.tally(ledger) == {
        "kept": 1,
        "discarded": 2,
        "crashed": 1,
    }


def test_the_tally_counts_only_this_run(tmp_path):
    """A night's panel must show the night's work, not every experiment ever."""
    ledger = tmp_path / "results.tsv"
    ledger.write_text(HEADER + "".join(ROWS), encoding="utf-8")
    # The header plus two rows already existed when this run started, so line 3
    # onward is this run's own work: the crash and the discard after it.
    assert autoresearch_status.tally(ledger, since_line=3) == {
        "kept": 0,
        "discarded": 1,
        "crashed": 1,
    }
    # And from the top, everything.
    assert sum(autoresearch_status.tally(ledger).values()) == 4


def test_a_void_row_counts_as_none_of_them(tmp_path):
    """Voiding withdraws a number. It is not a result."""
    ledger = tmp_path / "results.tsv"
    ledger.write_text(HEADER + "eee5555\t80.0\t0.0\tvoid\tessays 8: withdrawn\n",
                      encoding="utf-8")
    assert autoresearch_status.tally(ledger) == {
        "kept": 0,
        "discarded": 0,
        "crashed": 0,
    }


def test_a_missing_ledger_reports_zeroes_rather_than_failing(tmp_path):
    """The panel is a convenience. It must never take down a run."""
    assert autoresearch_status.tally(tmp_path / "absent.tsv") == {
        "kept": 0,
        "discarded": 0,
        "crashed": 0,
    }


def test_the_queue_publishes_the_counts_it_has():
    """The defect itself: the writer never wrote these keys."""
    source = (ROOT / "tools" / "autoresearch_queue.py").read_text(encoding="utf-8")
    assert "autoresearch_status.tally(since_line=started_at)" in source, (
        "the queue does not publish kept/discarded/crashed"
    )
    assert "last_result" in source, "the queue does not publish what just happened"
    assert "entry {completed + 1} of {total}" in source, (
        "the panel cannot show progress through the queue"
    )


def test_the_queue_takes_the_panel_down_when_it_finishes():
    """No work, no window. That is the whole mechanism."""
    source = (ROOT / "tools" / "autoresearch_queue.py").read_text(encoding="utf-8")
    assert "autoresearch_status.clear_status()" in source


def test_a_stale_status_file_is_not_believed():
    """A killed run cannot clear its own file."""
    assert autoresearch_status.STALE_AFTER_SECONDS > 0
    source = (ROOT / "tools" / "autoresearch_monitor.py").read_text(encoding="utf-8")
    assert "_is_stale" in source
    assert "STALE_AFTER_SECONDS" in source


def test_the_staleness_threshold_outlasts_a_normal_gap():
    """The runner replaces the file between entries, and an experiment takes
    five minutes of budget plus app restarts. A threshold shorter than a real
    gap would blink the panel out mid-run, which is the opposite failure."""
    assert autoresearch_status.STALE_AFTER_SECONDS >= 120


def test_a_fresh_stamp_is_believed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        autoresearch_status, "status_path", lambda: tmp_path / "status.json"
    )
    autoresearch_status.write_status({"phase": "reliability hard"})
    current = autoresearch_status.read_status()
    assert current is not None
    written = datetime.fromisoformat(current["stamp"])
    assert datetime.now() - written < timedelta(
        seconds=autoresearch_status.STALE_AFTER_SECONDS
    )
