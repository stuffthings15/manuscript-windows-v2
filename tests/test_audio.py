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
