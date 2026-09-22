from __future__ import annotations

import threading
import time

import numpy as np

from echo_link_core.correction import CorrectionResult
from echo_link_core.engine import DictationEngine
from echo_link_core.insertion import WindowSnapshot
from echo_link_core.models import DictationMode
from echo_link_core.storage import EchoLinkStore


class SequencedRecorder:
    def __init__(self) -> None:
        self.started = False
        self.starts = 0
        self.stops = 0

    def start(self, *_args, **_kwargs) -> None:
        assert not self.started
        self.started = True
        self.starts += 1

    def stop(self) -> np.ndarray:
        assert self.started
        self.started = False
        self.stops += 1
        return np.full(8000, self.stops, dtype=np.int16)

    def cancel(self) -> None:
        self.started = False


class SequencedTranscriber:
    def transcribe(self, audio, **_kwargs) -> str:
        return f"raw {int(np.asarray(audio)[0])}"

    def update_settings(self, _settings) -> None:
        pass

    def release_model(self) -> None:
        pass


class BlockingCorrection:
    def __init__(self) -> None:
        self.first_started = threading.Event()
        self.release_first = threading.Event()
        self.calls: list[str] = []
        self.concurrent = 0
        self.max_concurrent = 0
        self._lock = threading.Lock()

    def deterministic(self, raw_text: str, *_args, **_kwargs) -> CorrectionResult:
        return CorrectionResult(f"deterministic {raw_text}", "deterministic")

    def correct(self, raw_text: str, _mode=None, **_kwargs) -> CorrectionResult:
        with self._lock:
            self.calls.append(raw_text)
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            if raw_text == "raw 1":
                self.first_started.set()
                assert self.release_first.wait(2.0)
            return CorrectionResult(f"corrected {raw_text}", "ollama")
        finally:
            with self._lock:
                self.concurrent -= 1

    def update_settings(self, _settings) -> None:
        pass

    def preload(self, **_kwargs) -> None:
        pass


class InstantCorrection:
    def deterministic(self, raw_text: str, *_args, **_kwargs) -> CorrectionResult:
        return CorrectionResult(f"deterministic {raw_text}", "deterministic")

    def correct(self, raw_text: str, _mode=None, **_kwargs) -> CorrectionResult:
        return CorrectionResult(f"corrected {raw_text}", "ollama")

    def update_settings(self, _settings) -> None:
        pass

    def preload(self, **_kwargs) -> None:
        pass


class OrderedInserter:
    def __init__(self) -> None:
        self.insertions: list[tuple[WindowSnapshot, str]] = []

    def insert_into(self, window: WindowSnapshot, text: str):
        self.insertions.append((window, text))
        return object()

    def undo_last(self) -> bool:
        return bool(self.insertions)


def _engine(tmp_path, correction, *, event_callback=None, level_callback=None):
    store = EchoLinkStore(tmp_path / "dictation-queue.db")
    settings = store.get_settings()
    settings.mode = DictationMode.REFINE
    settings.streaming_enabled = False
    settings.append_trailing_space = False
    settings.keep_history = True
    store.save_settings(settings)
    recorder = SequencedRecorder()
    inserter = OrderedInserter()
    engine = DictationEngine(
        store,
        recorder=recorder,
        transcriber=SequencedTranscriber(),
        correction=correction,
        inserter=inserter,
        event_callback=event_callback,
        level_callback=level_callback,
    )
    engine.accept_ai_plan(
        pack_id="gemma4-e4b-qat-v1",
        model="gemma4:e4b-it-qat",
        policy_version=8,
    )
    return engine, recorder, inserter


def _record(engine: DictationEngine, target: WindowSnapshot) -> float:
    started = time.perf_counter()
    assert engine.start_recording(target_window=target)
    latency = time.perf_counter() - started
    assert engine.stop_recording()
    return latency


def test_capture_stays_available_and_jobs_finish_fifo_while_first_ai_blocks(tmp_path):
    correction = BlockingCorrection()
    engine, recorder, inserter = _engine(tmp_path, correction)
    targets = [
        WindowSnapshot(101, 501, "Editor one"),
        WindowSnapshot(102, 502, "Editor two"),
        WindowSnapshot(103, 503, "Editor three"),
    ]
    try:
        assert _record(engine, targets[0]) < 0.250
        assert correction.first_started.wait(1.0)
        assert engine.can_start_recording is True

        assert _record(engine, targets[1]) < 0.250
        assert _record(engine, targets[2]) < 0.250
        assert recorder.starts == recorder.stops == 3

        correction.release_first.set()
        assert engine.wait_until_idle(3.0)

        assert correction.calls == ["raw 1", "raw 2", "raw 3"]
        assert correction.max_concurrent == 1
        assert inserter.insertions == [
            (targets[0], "corrected raw 1"),
            (targets[1], "corrected raw 2"),
            (targets[2], "corrected raw 3"),
        ]
    finally:
        correction.release_first.set()
        engine.shutdown()


def test_refine_recording_preloads_ai_without_blocking_microphone(tmp_path):
    class PreloadingCorrection:
        def __init__(self) -> None:
            self.started = threading.Event()
            self.release = threading.Event()

        def update_settings(self, _settings) -> None:
            pass

        def preload(self, *, timeout) -> None:
            self.started.set()
            self.release.wait(timeout)

    correction = PreloadingCorrection()
    engine, _recorder, _inserter = _engine(tmp_path, correction)
    engine.accept_ai_plan(
        pack_id="gemma4-e4b-qat-v1",
        model="gemma4:e4b-it-qat",
        policy_version=8,
    )
    try:
        started = time.perf_counter()
        assert engine.start_recording(insert_result=False)
        assert time.perf_counter() - started < 0.100
        assert correction.started.wait(0.5)
        assert engine.cancel_recording()
    finally:
        correction.release.set()
        engine.shutdown()


def test_queued_status_is_emitted_before_a_fast_job_can_complete(tmp_path):
    events = []
    engine, _recorder, _inserter = _engine(
        tmp_path,
        InstantCorrection(),
        event_callback=events.append,
        level_callback=lambda value: time.sleep(0.05) if value == 0 else None,
    )
    try:
        assert engine.start_recording(insert_result=False)
        assert engine.stop_recording()
        assert engine.wait_until_idle(2.0)

        states = [event.state.value for event in events]
        assert states.index("transcribing") < states.index("complete")
        assert states[-1] == "complete"
    finally:
        engine.shutdown()
