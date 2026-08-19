"""The batch harness must report what the APP ran, not what it pressed.

Until 2026-08-18 the harness only ever pressed Replace. A full 20-essay run
issued 213 Polish requests and every one was command=Proofread, so "run the
essays through Word" could not see the tone feature and never had - every tone
number quoted to that point came from a headless probe instead.

The first run of the new --tone flag pressed nothing at all, because the tone
control is named differently on the card than in the source, and the run still
finished and looked healthy. The only thing that caught it was reading the
app's own log back. These tests cover that read-back, which is the part that
decides whether a tone run is a measurement or a story.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))


def _log(monkeypatch, tmp_path: Path, text: str) -> None:
    import run_essays_batch

    path = tmp_path / "echo-link.log"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(run_essays_batch, "LOG", path)


def test_commands_since_reports_what_the_app_actually_ran(monkeypatch, tmp_path):
    import run_essays_batch

    _log(
        monkeypatch,
        tmp_path,
        "\n".join(
            [
                "ignored line before the run",
                "... Writing HUD adaptive Polish request command=Proofread characters=10",
                "... Writing HUD adaptive Polish request command=Friendly characters=20",
                "... Writing HUD adaptive Polish request command=Friendly characters=30",
                "... unrelated",
            ]
        ),
    )

    assert run_essays_batch.commands_since(1) == {"Proofread": 1, "Friendly": 2}
    # Counting from the start of the run, not the start of the file: a stale
    # line from an earlier essay must not be credited to this one.
    assert run_essays_batch.commands_since(2) == {"Friendly": 2}


def test_a_tone_run_that_only_proofread_is_visible_as_such(monkeypatch, tmp_path):
    """The failure that actually happened, stated as a test.

    The harness selected nothing, the app proofread every section, and the run
    reported "complete". This is what the read-back sees in that case.
    """
    import run_essays_batch

    _log(
        monkeypatch,
        tmp_path,
        "\n".join(
            "... Writing HUD adaptive Polish request command=Proofread characters=1"
            for _ in range(11)
        ),
    )

    ran = run_essays_batch.commands_since(0)
    assert ran == {"Proofread": 11}
    assert "Friendly" not in ran


def test_rules_fallbacks_are_counted_from_the_app_log(monkeypatch, tmp_path):
    """Sections that got no AI at all, which is where the score goes."""
    import run_essays_batch

    _log(
        monkeypatch,
        tmp_path,
        "\n".join(
            [
                "before the run: Writing HUD section fell back to rules reason=x",
                "... Writing HUD section fell back to rules command=Proofread reason=y",
                "... nothing of interest",
                "... Writing HUD section fell back to rules command=Proofread reason=z",
            ]
        ),
    )

    assert run_essays_batch.rules_fallbacks_since(1) == 2
    assert run_essays_batch.rules_fallbacks_since(0) == 3


def test_a_missing_log_is_not_an_exception(monkeypatch, tmp_path):
    """A harness that crashes on a missing log reports nothing at all."""
    import run_essays_batch

    monkeypatch.setattr(run_essays_batch, "LOG", tmp_path / "absent.log")
    assert run_essays_batch.commands_since(0) == {}
    assert run_essays_batch.rules_fallbacks_since(0) == 0
