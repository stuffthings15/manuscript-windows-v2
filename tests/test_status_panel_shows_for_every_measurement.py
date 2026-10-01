"""If AutoResearch is measuring, the panel must be showing.

Curtis, 2026-09-02: "The auto research message in the top right-hand corner in
green is not available. It is not showing, and it should be showing if we are
conducting auto research."

He was right, and it was a process failure rather than a bug. Only the cycles'
`main()` published the panel, so every baseline measured that night went through
a throwaway script in the temporary directory that called `measure()` directly.
It showed nothing, and - worse - a script outside the repository cannot be
reviewed or re-run by anyone else, so the rows it wrote were unverifiable.

The panel is the promise that a run is real. These tests bind it to the
measurement rather than to a command-line wrapper, so the failure cannot repeat.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import autoresearch_status  # noqa: E402

MEASURING_TOOLS = (
    "autoresearch_cycle.py",
    "autoresearch_tone_cycle.py",
    "autoresearch_reliability_cycle.py",
    "autoresearch_latency_cycle.py",
    "tone_baselines.py",
    "reliability_baselines.py",
)


def test_every_measuring_tool_publishes_the_panel():
    """Directly, or by launching something that does.

    `autoresearch_cycle` delegates: it runs `autoresearch_run.py` as a
    subprocess and that publishes for the whole Word run. Requiring every tool
    to publish for itself would have failed a tool that is already correct, so
    the assertion is about the PANEL APPEARING, not about which file calls the
    writer.
    """
    for name in MEASURING_TOOLS:
        source = (ROOT / "tools" / name).read_text(encoding="utf-8")
        publishes = "write_status" in source or "autoresearch_status.showing" in source
        delegates = "autoresearch_run.py" in source
        assert publishes or delegates, (
            f"{name} measures without the panel ever appearing"
        )


def test_a_baseline_runner_exists_in_the_repository_for_each_metric():
    """A measurement that only exists in a temporary script is not reproducible."""
    for name in ("tone_baselines.py", "reliability_baselines.py"):
        assert (ROOT / "tools" / name).is_file()


def test_showing_publishes_then_clears(tmp_path, monkeypatch):
    monkeypatch.setattr(
        autoresearch_status, "status_path", lambda: tmp_path / "status.json"
    )
    assert autoresearch_status.read_status() is None

    with autoresearch_status.showing(phase="reliability shapes", essays="1-5"):
        current = autoresearch_status.read_status()
        assert current is not None
        assert current["phase"] == "reliability shapes"

    assert autoresearch_status.read_status() is None, "the panel was left up"


def test_showing_clears_even_when_the_measurement_raises(tmp_path, monkeypatch):
    """A panel left up after a crash is a lie about what the machine is doing."""
    monkeypatch.setattr(
        autoresearch_status, "status_path", lambda: tmp_path / "status.json"
    )

    try:
        with autoresearch_status.showing(phase="reliability shapes"):
            raise RuntimeError("the Word run died")
    except RuntimeError:
        pass

    assert autoresearch_status.read_status() is None


def test_showing_does_not_swallow_the_failure(tmp_path, monkeypatch):
    """Clearing the panel must never hide why a run stopped."""
    monkeypatch.setattr(
        autoresearch_status, "status_path", lambda: tmp_path / "status.json"
    )
    raised = False
    try:
        with autoresearch_status.showing(phase="x"):
            raise ValueError("boom")
    except ValueError:
        raised = True
    assert raised
