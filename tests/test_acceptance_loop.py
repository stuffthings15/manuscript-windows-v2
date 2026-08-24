"""The loop's own rules, tested without driving Word.

Everything expensive in the loop belongs to `release_acceptance.py`. What is
tested here is the small amount of judgement the loop adds: whether it may
start, when it may stop, and whether two failures are the same failure.

These matter because the loop runs unattended. A lock that does not hold lets
two harnesses drive one app, which on 2026-08-23 produced a run reporting that
the tone control was unreachable and the app was stuck on Friendly - neither
true, both caused by a second harness in the same window.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))


def _sandbox(monkeypatch, tmp_path: Path):
    import acceptance_loop

    monkeypatch.setattr(acceptance_loop, "EVIDENCE", tmp_path)
    monkeypatch.setattr(acceptance_loop, "LOCK", tmp_path / "loop.lock")
    monkeypatch.setattr(acceptance_loop, "STATE", tmp_path / "loop-state.json")
    monkeypatch.setattr(acceptance_loop, "LOG", tmp_path / "loop-log.jsonl")
    return acceptance_loop


def test_the_loop_takes_a_lock_and_releases_it(monkeypatch, tmp_path):
    loop = _sandbox(monkeypatch, tmp_path)

    assert loop.take_lock() is True
    held = json.loads((tmp_path / "loop.lock").read_text(encoding="utf-8"))
    assert held["pid"] == os.getpid()

    loop.release_lock()
    assert not (tmp_path / "loop.lock").exists()


def test_a_live_lock_from_another_process_refuses_the_start(monkeypatch, tmp_path):
    """Two harnesses on one app is the failure this prevents."""
    loop = _sandbox(monkeypatch, tmp_path)
    (tmp_path / "loop.lock").write_text(
        json.dumps({"pid": 4242, "since": "now"}), encoding="utf-8"
    )
    monkeypatch.setattr(loop, "_pid_alive", lambda pid: True)

    assert loop.take_lock() is False


def test_a_stale_lock_does_not_block_the_loop_for_ever(monkeypatch, tmp_path):
    """A killed run must not leave the loop unable to start again."""
    loop = _sandbox(monkeypatch, tmp_path)
    (tmp_path / "loop.lock").write_text(
        json.dumps({"pid": 4242, "since": "then"}), encoding="utf-8"
    )
    monkeypatch.setattr(loop, "_pid_alive", lambda pid: False)

    assert loop.take_lock() is True


def test_the_same_gate_failing_twice_has_the_same_fingerprint(monkeypatch, tmp_path):
    """Movement is judged on WHAT failed, not on the number beside it.

    A mean creeping from 84.5 to 84.9 is the same failure, and treating it as
    progress is how a loop spends a night on a disproved hypothesis.
    """
    loop = _sandbox(monkeypatch, tmp_path)

    first = loop.fingerprint(
        {"failures": ["coherent mean 84.5 is below 95.0", "tone Clear at 71.4% is below 95.0%"]}
    )
    second = loop.fingerprint(
        {"failures": ["coherent mean 84.9 is below 95.0", "tone Clear at 72.0% is below 95.0%"]}
    )

    assert first == second


def test_a_different_failure_is_a_different_fingerprint(monkeypatch, tmp_path):
    loop = _sandbox(monkeypatch, tmp_path)

    assert loop.fingerprint({"failures": ["coherent mean 84.5 is below 95.0"]}) != loop.fingerprint(
        {"failures": ["ruff is not clean"]}
    )


def test_a_tier_that_wrote_no_evidence_is_a_failure_with_a_reason(monkeypatch, tmp_path):
    """A crashed tier must not read as a missing measurement.

    Without this the loop would see no evidence file, record nothing, and go
    round again - repeating a crash for ever and reporting nothing about it.
    """
    loop = _sandbox(monkeypatch, tmp_path)

    class _Completed:
        returncode = 3

    monkeypatch.setattr(loop.subprocess, "run", lambda *a, **k: _Completed())

    results = loop.run_tier("fast", 7)

    assert results["passed"] is False
    assert "wrote no evidence file" in results["failures"][0]
    assert "exit=3" in results["failures"][0]


def test_the_loop_records_a_state_a_person_can_read(monkeypatch, tmp_path):
    loop = _sandbox(monkeypatch, tmp_path)

    loop.record(
        3,
        {"tier": "full", "passed": False, "seconds": 12.0, "failures": ["x is below y"]},
        {"iteration": 3, "next": "fix the failures above, then run again"},
    )

    state = json.loads((tmp_path / "loop-state.json").read_text(encoding="utf-8"))
    assert state["next"]
    rows = (tmp_path / "loop-log.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(rows[-1])["iteration"] == 3
