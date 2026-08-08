from __future__ import annotations

import numpy as np

from echo_link_core.audio import MicrophoneRecorder


class FakeStream:
    def __init__(self):
        self.stopped = False
        self.closed = False

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


def test_recording_snapshot_returns_current_audio_without_stopping():
    recorder = MicrophoneRecorder(sample_rate=16000)
    recorder._recording = True
    recorder._callback(np.ones((400, 1), dtype=np.int16), 400, None, None)
    recorder._callback(np.full((400, 1), 2, dtype=np.int16), 400, None, None)
    snapshot = recorder.snapshot()
    assert snapshot.size == 800
    assert snapshot[0] == 1
    assert snapshot[-1] == 2
    assert recorder.is_recording is True
    final = recorder.stop()
    assert final.size == 800


def test_pre_roll_keeps_only_latest_memory_audio_and_leads_recording():
    recorder = MicrophoneRecorder(sample_rate=1000, block_ms=100)
    stream = FakeStream()
    recorder._armed = True
    recorder._stream = stream
    recorder._device_index = 3
    recorder._pre_roll_samples = 500
    recorder._callback(np.ones((300, 1), dtype=np.int16), 300, None, None)
    recorder._callback(np.full((300, 1), 2, dtype=np.int16), 300, None, None)

    recorder.start(3)
    recorder._callback(np.full((100, 1), 3, dtype=np.int16), 100, None, None)
    final = recorder.stop()

    assert final.size == 600
    assert np.count_nonzero(final == 1) == 200
    assert np.count_nonzero(final == 2) == 300
    assert np.count_nonzero(final == 3) == 100
    assert stream.closed is False
    recorder.cancel()
    assert stream.closed is True


def _fake_microphones(monkeypatch, names):
    """Stand in for the devices sounddevice reports right now."""
    from echo_link_core import audio

    monkeypatch.setattr(
        audio,
        "list_microphones",
        lambda: [
            audio.MicrophoneInfo(index=index, name=name, default_sample_rate=16000)
            for index, name in names
        ],
    )


def test_saved_microphone_is_found_after_its_index_moved(monkeypatch):
    """A headset connecting renumbers everything after it."""
    from echo_link_core.audio import resolve_microphone

    _fake_microphones(
        monkeypatch,
        [(1, "Headset (JBL Live 780NC)"), (8, "Microphone Array (Intel)")],
    )
    assert resolve_microphone(2, "Microphone Array (Intel)") == 8


def test_a_disconnected_microphone_falls_back_to_the_system_default(monkeypatch):
    """The whole bug: this returned a stale index and dictation reported no
    microphone while a working array sat unused. None means system default."""
    from echo_link_core.audio import resolve_microphone

    _fake_microphones(monkeypatch, [(1, "Microphone Array (Intel)")])
    assert resolve_microphone(9, "Headset (OpenRun Pro 2 by Shokz)") is None


def test_a_stale_index_without_a_name_is_not_trusted(monkeypatch):
    """Settings written before names were stored."""
    from echo_link_core.audio import resolve_microphone

    _fake_microphones(monkeypatch, [(1, "Microphone Array (Intel)")])
    assert resolve_microphone(9, "") is None
    assert resolve_microphone(1, "") == 1


def test_no_microphones_at_all_resolves_to_the_system_default(monkeypatch):
    from echo_link_core.audio import resolve_microphone

    _fake_microphones(monkeypatch, [])
    assert resolve_microphone(1, "Microphone Array (Intel)") is None


def test_microphone_name_survives_a_settings_round_trip():
    from echo_link_core.models import DictationSettings

    settings = DictationSettings(microphone_index=8, microphone_name="Array")
    assert DictationSettings.from_dict(settings.to_dict()).microphone_name == "Array"


def test_settings_written_before_names_still_load():
    from echo_link_core.models import DictationSettings

    assert DictationSettings.from_dict({"microphone_index": 3}).microphone_name == ""
