from __future__ import annotations

from echo_link_core.silence import SilenceAutoStopTracker


def test_silence_tracker_waits_for_real_voice_before_stopping():
    tracker = SilenceAutoStopTracker(
        2.5,
        voice_threshold=0.04,
        minimum_voice_seconds=0.12,
    )
    tracker.observe(0.0, now=0.0)
    assert tracker.should_stop(now=20.0) is False
    tracker.observe(0.10, now=20.0)
    tracker.observe(0.10, now=20.13)
    assert tracker.speech_seen is True
    assert tracker.should_stop(now=22.62) is False
    assert tracker.should_stop(now=22.64) is True


def test_silence_tracker_ignores_short_noise_spike():
    tracker = SilenceAutoStopTracker(1.0, minimum_voice_seconds=0.12)
    tracker.observe(0.5, now=1.0)
    tracker.observe(0.0, now=1.05)
    assert tracker.speech_seen is False
    assert tracker.should_stop(now=5.0) is False
