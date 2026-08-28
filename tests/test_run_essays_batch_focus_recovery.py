"""The batch harness must take focus back, and must never guess while it does.

Measured on 2026-08-27: a twenty-essay run lost 4 of its first 10 essays and
every failure was focus. Three were simply the HUD not being there - "the HUD
was gone at press 1", "the HUD could not be found within 8s after press 15",
and one sitting at view=badge with provenance=waiting - and the app's own log
named the cause at the exact second of the first one, nine times across the
run: `focus detected title='Claude'`.

The app is right to follow focus; that is the dictation feature. The harness
was wrong to give up, and wrong in one specific way that these tests pin down:
recovering must not silently continue against whatever review happens to be on
screen afterwards. A review built from another window, clicked anyway, is how a
run once reported "sections=5 accepted=5" against 26 characters of chat text
while the essay came back byte-identical.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import run_essays_batch  # noqa: E402


class _Hud:
    """Stands in for the HUD window object, which only exists on a live run."""


def test_focus_is_taken_back_and_the_hud_is_returned(monkeypatch):
    focused: list[str] = []
    monkeypatch.setattr(
        run_essays_batch, "focus_named_word_window", lambda stem: focused.append(stem)
    )
    hud = _Hud()
    monkeypatch.setattr(run_essays_batch, "hud_window", lambda _seconds: hud)
    monkeypatch.setattr(
        run_essays_batch,
        "captured_document_title",
        lambda *args, **kwargs: "07_community_gardens  -  Word",
    )
    monkeypatch.setattr(run_essays_batch.time, "sleep", lambda _seconds: None)

    report: dict = {}
    recovered = run_essays_batch.recover_hud("07_community_gardens", report)

    assert recovered is hud
    assert focused == ["07_community_gardens"]
    # Recorded, so a recovered run is never mistaken for a clean one.
    assert report["hud_recoveries"] == 1


def test_a_hud_showing_another_document_is_refused(monkeypatch):
    """The failure this whole guard exists for.

    The HUD comes back, so the naive check passes, but it is showing the Claude
    window. Returning it here would let the caller click Replace against
    somebody else's text and count the presses as progress.
    """
    monkeypatch.setattr(run_essays_batch, "focus_named_word_window", lambda stem: 1)
    monkeypatch.setattr(run_essays_batch, "hud_window", lambda _seconds: _Hud())
    monkeypatch.setattr(
        run_essays_batch, "captured_document_title", lambda *a, **k: "Claude"
    )
    monkeypatch.setattr(run_essays_batch.time, "sleep", lambda _seconds: None)

    report: dict = {}

    assert run_essays_batch.recover_hud("07_community_gardens", report) is None
    assert "hud_recoveries" not in report


def test_recovery_gives_up_rather_than_hanging(monkeypatch):
    """A HUD that never returns must end the essay, not the batch."""
    attempts: list[int] = []

    def _never(_seconds):
        attempts.append(1)
        return None

    monkeypatch.setattr(run_essays_batch, "focus_named_word_window", lambda stem: 1)
    monkeypatch.setattr(run_essays_batch, "hud_window", _never)
    monkeypatch.setattr(run_essays_batch.time, "sleep", lambda _seconds: None)

    report: dict = {}

    assert run_essays_batch.recover_hud("07_community_gardens", report) is None
    assert len(attempts) == 3


def test_recovery_does_not_restart_the_review(monkeypatch):
    """Mid-run, pressing the hotkey again would be worse than failing.

    A press re-captures the document and restarts the review at section one.
    The presses that follow would land on a fresh review while the counters
    kept climbing, so the run would finish and report a number built from two
    different reviews. A failure is recoverable; a wrong number is not.
    """
    pressed: list[int] = []
    monkeypatch.setattr(run_essays_batch, "press_hotkey", lambda: pressed.append(1))
    monkeypatch.setattr(run_essays_batch, "focus_named_word_window", lambda stem: 1)
    monkeypatch.setattr(run_essays_batch, "hud_window", lambda _seconds: _Hud())
    monkeypatch.setattr(
        run_essays_batch, "captured_document_title", lambda *a, **k: "07_x  -  Word"
    )
    monkeypatch.setattr(run_essays_batch.time, "sleep", lambda _seconds: None)

    run_essays_batch.recover_hud("07_x", {})

    assert pressed == []


def test_focus_is_reasserted_before_a_press_when_another_window_has_it(monkeypatch):
    """The failure recover_hud cannot see: the HUD is there, pointing elsewhere.

    The first AutoResearch experiment died this way - "no Replace button at
    press 1; section 1/4, ready 0, view=badge" with `focus detected
    title='Claude'` in the app log four times. hud_window returned a HUD, so no
    recovery fired, and the essay came back with unknown=54 and unchanged=11.
    """
    import run_essays_batch as batch

    refocused: list[str] = []
    monkeypatch.setattr(batch, "focus_named_word_window", refocused.append)
    monkeypatch.setattr(batch, "_foreground_title", lambda: "Claude", raising=False)

    batch._hold_focus("01_urban_beekeeping")

    assert refocused == ["01_urban_beekeeping"]


def test_focus_is_left_alone_when_the_document_already_has_it(monkeypatch):
    """Re-focusing on every press regardless would be its own disruption."""
    import run_essays_batch as batch

    refocused: list[str] = []
    monkeypatch.setattr(batch, "focus_named_word_window", refocused.append)
    monkeypatch.setattr(
        batch,
        "_foreground_title",
        lambda: "01_urban_beekeeping  -  Compatibility Mode - Word",
        raising=False,
    )

    batch._hold_focus("01_urban_beekeeping")

    assert refocused == []
