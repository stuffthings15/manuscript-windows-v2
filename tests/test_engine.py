from __future__ import annotations

import time

import numpy as np
import pytest

from echo_link_core.accepted_inference import AcceptedInferenceCoordinator
from echo_link_core.correction import CorrectionPipeline, CorrectionResult
from echo_link_core.engine import DictationEngine
from echo_link_core.insertion import PartialTextInsertionError
from echo_link_core.models import DictationMode, DictationSettings, InsertionMode, SessionState
from echo_link_core.storage import EchoLinkStore
from echo_link_core.polish_types import (
    ExecutionPlan,
    GenerationResult,
    PolishMode,
    PolishTask,
    RuntimeBackend,
    RuntimeStatus,
)


class SharedAIRuntime:
    runtime_id = "test:engine-shared-ai"

    def __init__(self):
        self.calls = []

    def generate(self, request):
        self.calls.append(request)
        return GenerationResult(
            "Hello there.",
            request.plan.model,
            RuntimeBackend.CUDA,
            0.01,
        )

    def status(self, model, *, timeout=1.0):
        return RuntimeStatus(
            model=model,
            loaded=True,
            backend=RuntimeBackend.CUDA,
            model_bytes=4_000,
            gpu_bytes=4_000,
        )

    def close(self):
        pass


def connect_accepted_ai(engine):
    runtime = SharedAIRuntime()
    coordinator = AcceptedInferenceCoordinator(runtime)
    plan = ExecutionPlan(
        policy_version=8,
        mode=PolishMode.AUTO,
        task=PolishTask.PROOFREAD,
        use_llm=True,
        pack_id="gemma4-e4b-qat-v1",
        model="gemma4:e4b-it-qat",
        backend=RuntimeBackend.CUDA,
        context_window=2048,
        max_output_tokens=512,
        require_full_gpu=True,
    )
    assert coordinator.accept_verified(plan)
    engine.set_ai_coordinator(coordinator)
    return coordinator, runtime, plan


class FakeRecorder:
    def __init__(self):
        self.started = False

    def start(self, *_args, **_kwargs):
        self.started = True

    def stop(self):
        self.started = False
        return np.ones(8000, dtype=np.int16)

    def cancel(self):
        self.started = False


class FakeTranscriber:
    def __init__(self, text="hello hello world"):
        self.text = text

    def transcribe(self, *_args, **_kwargs):
        return self.text

    def update_settings(self, _settings):
        pass

    def release_model(self):
        pass


class StreamingRecorder(FakeRecorder):
    def snapshot(self):
        return np.ones(16000, dtype=np.int16)


class StreamingTranscriber(FakeTranscriber):
    def transcribe_partial(self, _audio):
        return "hello live preview"


class FakeInserter:
    def __init__(self):
        self.inserted = []

    def insert(self, text):
        self.inserted.append(text)
        return object()

    def replace_if_safe(self, _record, text):
        self.inserted.append(text)
        return object()

    def insert_into(self, _window, text):
        self.inserted.append(text)
        return object()

    def undo_last(self):
        return bool(self.inserted)


class FailingInserter(FakeInserter):
    def insert(self, _text):
        raise RuntimeError("Paste failed")

    def insert_into(self, _window, _text):
        raise RuntimeError("Paste failed")


class PartiallyFailingInserter(FakeInserter):
    def insert_into(self, _window, text):
        raise PartialTextInsertionError(
            text,
            len("Hello "),
            reason="Direct text insertion stopped partway through.",
        )


class RefusingInserter(FakeInserter):
    def insert_into(self, _window, _text):
        return None


class TargetTrackingInserter(FakeInserter):
    def __init__(self):
        super().__init__()
        self.targets = []

    def insert_into(self, window, text):
        self.targets.append(window)
        return super().insert_into(window, text)


def _wait(engine):
    deadline = time.monotonic() + 2
    while engine.state not in (SessionState.COMPLETE, SessionState.ERROR) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert engine.state is SessionState.COMPLETE


def test_engine_applies_persisted_insertion_mode_and_live_updates(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    settings = store.get_settings()
    settings.insertion_mode = InsertionMode.DIRECT
    store.save_settings(settings)
    engine = DictationEngine(
        store,
        recorder=FakeRecorder(),
        transcriber=FakeTranscriber(),
        correction=CorrectionPipeline(store.vocabulary_map, settings),
    )

    assert engine.inserter.mode is InsertionMode.DIRECT

    settings.insertion_mode = InsertionMode.CLIPBOARD
    engine.update_settings(settings)
    assert engine.inserter.mode is InsertionMode.CLIPBOARD


def test_engine_keeps_ai_empty_until_a_hardware_plan_is_accepted(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    persisted = store.get_settings()
    persisted.ollama_model = "gemma4:e2b-it-qat"
    persisted.polish_model_pack_id = "gemma4-e2b-qat-v1"
    store.save_settings(persisted)
    pipeline = CorrectionPipeline(store.vocabulary_map, persisted)

    engine = DictationEngine(
        store,
        recorder=FakeRecorder(),
        transcriber=FakeTranscriber(),
        correction=pipeline,
    )

    assert engine.settings.ollama_model == ""
    assert engine.settings.polish_model_pack_id == ""
    assert pipeline.accepted_model == ""
    assert pipeline.preload(timeout=1.0) is False

    attempted_restore = DictationSettings.from_dict(store.get_settings().to_dict())
    engine.update_settings(attempted_restore)
    assert engine.settings.ollama_model == ""
    assert engine.settings.polish_model_pack_id == ""


def test_engine_rejects_a_model_without_the_shared_coordinator(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    pipeline = CorrectionPipeline(store.vocabulary_map, store.get_settings())
    engine = DictationEngine(
        store,
        recorder=FakeRecorder(),
        transcriber=FakeTranscriber(),
        correction=pipeline,
    )

    with pytest.raises(ValueError, match="shared coordinator"):
        engine.accept_ai_plan(
            pack_id="gemma4-e4b-qat-v1",
            model="gemma4:e4b-it-qat",
            policy_version=8,
        )

    assert pipeline.accepted_model == ""
    assert engine.settings.ollama_model == ""


def test_accuracy_never_calls_ai_when_shared_plan_is_not_ready(tmp_path):
    class GuardedCorrection:
        def __init__(self):
            self.correct_calls = 0

        def update_settings(self, _settings):
            pass

        def deterministic(self, raw_text, *_args, **_kwargs):
            return CorrectionResult("Deterministic text.", "deterministic")

        def correct(self, *_args, **_kwargs):
            self.correct_calls += 1
            raise AssertionError("Accuracy called AI without an accepted plan")

        def preload(self, **_kwargs):
            raise AssertionError("Accuracy preloaded AI without an accepted plan")

    store = EchoLinkStore(tmp_path / "echo-link.db")
    settings = store.get_settings()
    settings.mode = DictationMode.ACCURACY
    settings.streaming_enabled = False
    settings.append_trailing_space = False
    store.save_settings(settings)
    correction = GuardedCorrection()
    events = []
    engine = DictationEngine(
        store,
        recorder=FakeRecorder(),
        transcriber=FakeTranscriber("raw text"),
        correction=correction,
        inserter=FakeInserter(),
        event_callback=events.append,
    )

    assert engine.start_recording()
    assert engine.stop_recording()
    _wait(engine)

    assert correction.correct_calls == 0
    assert events[-1].final_text == "Deterministic text."


def test_engine_persists_and_shares_only_the_accepted_ai_plan(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    pipeline = CorrectionPipeline(store.vocabulary_map, store.get_settings())
    engine = DictationEngine(
        store,
        recorder=FakeRecorder(),
        transcriber=FakeTranscriber(),
        correction=pipeline,
    )

    _coordinator, _runtime, plan = connect_accepted_ai(engine)

    persisted = store.get_settings()
    assert engine.settings.ollama_model == plan.model
    assert engine.settings.polish_model_pack_id == plan.pack_id
    assert engine.settings.polish_policy_version == 8
    assert pipeline.accepted_model == plan.model
    assert persisted.ollama_model == plan.model
    assert persisted.polish_model_pack_id == plan.pack_id

    attempted_override = DictationSettings.from_dict(engine.settings.to_dict())
    attempted_override.ollama_model = "gemma4:e2b-it-qat"
    attempted_override.polish_model_pack_id = "gemma4-e2b-qat-v1"
    attempted_override.polish_policy_version = 1
    engine.update_settings(attempted_override)
    assert engine.settings.ollama_model == plan.model
    assert engine.settings.polish_model_pack_id == plan.pack_id
    assert engine.settings.polish_policy_version == 8
    assert pipeline.accepted_model == plan.model


def test_ai_plan_acceptance_merges_with_the_latest_user_settings(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    pipeline = CorrectionPipeline(store.vocabulary_map, store.get_settings())
    engine = DictationEngine(
        store,
        recorder=FakeRecorder(),
        transcriber=FakeTranscriber(),
        correction=pipeline,
    )
    changed = DictationSettings.from_dict(engine.settings.to_dict())
    changed.keep_history = False
    engine.update_settings(changed)

    connect_accepted_ai(engine)

    assert engine.settings.keep_history is False
    assert store.get_settings().keep_history is False


def test_dictation_revalidates_and_clears_a_stale_ai_plan_before_use(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    settings = store.get_settings()
    settings.mode = DictationMode.ACCURACY
    settings.streaming_enabled = False
    settings.append_trailing_space = False
    pipeline = CorrectionPipeline(store.vocabulary_map, settings)
    events = []
    engine = DictationEngine(
        store,
        recorder=FakeRecorder(),
        transcriber=FakeTranscriber("hello there"),
        correction=pipeline,
        inserter=FakeInserter(),
        event_callback=events.append,
    )
    _coordinator, runtime, _plan = connect_accepted_ai(engine)
    engine.set_ai_plan_validator(lambda *_args: False)
    engine.update_settings(settings)

    assert engine.start_recording()
    assert engine.stop_recording()
    _wait(engine)

    assert runtime.calls == []
    assert events[-1].final_text == "Hello there."
    assert engine.settings.ollama_model == ""
    assert pipeline.accepted_model == ""


def test_manual_recording_returns_text_without_inserting(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    inserter = FakeInserter()
    events = []
    engine = DictationEngine(
        store,
        recorder=FakeRecorder(),
        transcriber=FakeTranscriber(),
        correction=CorrectionPipeline(store.vocabulary_map, store.get_settings()),
        inserter=inserter,
        event_callback=events.append,
    )
    assert engine.start_recording(insert_result=False)
    assert engine.stop_recording()
    _wait(engine)
    assert inserter.inserted == []
    assert events[-1].final_text == "Hello world. "


def test_recording_stops_automatically_at_safe_limit(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    settings = store.get_settings()
    settings.max_recording_seconds = 0.05
    store.save_settings(settings)
    recorder = FakeRecorder()
    engine = DictationEngine(
        store,
        recorder=recorder,
        transcriber=FakeTranscriber(),
        correction=CorrectionPipeline(store.vocabulary_map, settings),
        inserter=FakeInserter(),
    )
    assert engine.start_recording(insert_result=False)
    _wait(engine)
    assert recorder.started is False


def test_hands_free_recording_stops_after_speech_then_silence(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    settings = store.get_settings()
    settings.streaming_enabled = False
    settings.silence_timeout_seconds = 0.25
    store.save_settings(settings)
    recorder = FakeRecorder()
    events = []
    engine = DictationEngine(
        store,
        recorder=recorder,
        transcriber=FakeTranscriber(),
        correction=CorrectionPipeline(store.vocabulary_map, settings),
        inserter=FakeInserter(),
        event_callback=events.append,
    )
    assert engine.start_recording(insert_result=False, hands_free=True)
    assert engine.hands_free_session is True
    engine._emit_level(0.2)
    time.sleep(0.13)
    engine._emit_level(0.2)
    _wait(engine)
    assert recorder.started is False
    assert any("Silence detected" in event.message for event in events)


def test_escape_cancel_discards_hands_free_audio(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    recorder = FakeRecorder()
    inserter = FakeInserter()
    events = []
    engine = DictationEngine(
        store,
        recorder=recorder,
        transcriber=FakeTranscriber(),
        correction=CorrectionPipeline(store.vocabulary_map, store.get_settings()),
        inserter=inserter,
        event_callback=events.append,
    )
    assert engine.start_recording(hands_free=True)
    assert engine.cancel_recording()
    assert engine.state is SessionState.READY
    assert recorder.started is False
    assert inserter.inserted == []
    assert events[-1].message == "Hands-free recording canceled."


def test_hotkey_recording_inserts_and_saves_enabled_history(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    settings = store.get_settings()
    settings.keep_history = True
    settings.mode = DictationMode.FAST
    store.save_settings(settings)
    inserter = FakeInserter()
    engine = DictationEngine(
        store,
        recorder=FakeRecorder(),
        transcriber=FakeTranscriber("echo link comma ready"),
        correction=CorrectionPipeline(store.vocabulary_map, settings),
        inserter=inserter,
    )
    assert engine.start_recording()
    assert engine.stop_recording()
    _wait(engine)
    assert inserter.inserted == ["Echo link, ready. "]
    assert store.list_history()[0]["inserted"] == 1


def test_enabled_speech_logging_keeps_actual_audio_and_transcript_stages_encrypted(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    settings = store.get_settings()
    settings.speech_logging_enabled = True
    settings.speech_log_retention_days = 15
    settings.streaming_enabled = False
    store.save_settings(settings)
    engine = DictationEngine(
        store,
        recorder=FakeRecorder(),
        transcriber=FakeTranscriber("we should go. to the store"),
        correction=CorrectionPipeline(store.vocabulary_map, settings),
        inserter=FakeInserter(),
    )
    assert engine.start_recording(insert_result=False)
    assert engine.stop_recording()
    _wait(engine)
    row = store.get_speech_improvement_log(store.list_speech_improvement_logs()[0]["id"])
    assert row is not None
    assert len(row["audio_pcm"]) == 16000
    assert row["raw_text"] == "we should go. to the store"
    assert row["analysis"]["counts"]["speech_recognition"] == 1


def test_developer_mode_inserts_once_without_refinement(tmp_path):
    class ForbiddenRefiner:
        def refine(self, *_args, **_kwargs):
            raise AssertionError("Developer mode must not refine")

    store = EchoLinkStore(tmp_path / "echo-link.db")
    settings = store.get_settings()
    settings.mode = DictationMode.DEVELOPER
    settings.streaming_enabled = False
    store.save_settings(settings)
    inserter = FakeInserter()
    events = []
    engine = DictationEngine(
        store,
        recorder=FakeRecorder(),
        transcriber=FakeTranscriber(
            "def camel case get user open paren close paren colon new line indent return one"
        ),
        correction=CorrectionPipeline(
            store.vocabulary_map,
            settings,
            ForbiddenRefiner(),
        ),
        inserter=inserter,
        event_callback=events.append,
    )

    assert engine.start_recording()
    assert engine.stop_recording()
    _wait(engine)
    assert inserter.inserted == ["def getUser():\n    return one "]
    assert events[-1].state is SessionState.COMPLETE
    assert not any(event.state is SessionState.REFINING for event in events)


def test_hotkey_insertion_returns_to_window_captured_at_recording_start(tmp_path):
    from echo_link_core.insertion import WindowSnapshot

    store = EchoLinkStore(tmp_path / "echo-link.db")
    target = WindowSnapshot(123, 456, "Target editor")
    inserter = TargetTrackingInserter()
    engine = DictationEngine(
        store,
        recorder=FakeRecorder(),
        transcriber=FakeTranscriber("hello target"),
        correction=CorrectionPipeline(store.vocabulary_map, store.get_settings()),
        inserter=inserter,
    )
    assert engine.start_recording(target_window=target)
    assert engine.stop_recording()
    _wait(engine)
    assert inserter.targets == [target]
    assert inserter.inserted == ["Hello target. "]


def test_requested_insert_reports_target_refusal_as_error_with_copyable_text(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    events = []
    engine = DictationEngine(
        store,
        recorder=FakeRecorder(),
        transcriber=FakeTranscriber("hello blocked target"),
        correction=CorrectionPipeline(store.vocabulary_map, store.get_settings()),
        inserter=RefusingInserter(),
        event_callback=events.append,
    )

    assert engine.start_recording()
    assert engine.stop_recording()
    deadline = time.monotonic() + 2
    while engine.state is not SessionState.ERROR and time.monotonic() < deadline:
        time.sleep(0.01)

    assert engine.state is SessionState.ERROR
    assert "captured target was unavailable" in events[-1].message
    assert events[-1].final_text.strip() == "Hello blocked target."


def test_review_mode_waits_for_acceptance_then_inserts(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    settings = store.get_settings()
    settings.review_before_insert = True
    settings.keep_history = True
    store.save_settings(settings)
    inserter = FakeInserter()
    events = []
    engine = DictationEngine(
        store,
        recorder=FakeRecorder(),
        transcriber=FakeTranscriber("hello world"),
        correction=CorrectionPipeline(store.vocabulary_map, settings),
        inserter=inserter,
        event_callback=events.append,
    )

    assert engine.start_recording()
    assert engine.stop_recording()
    _wait(engine)
    assert inserter.inserted == []
    assert engine.has_pending_review is True
    assert events[-1].review_pending is True
    assert engine.start_recording() is False

    assert engine.complete_review("Hello world.", decision="inserted") is True
    assert inserter.inserted == ["Hello world."]
    assert engine.has_pending_review is False
    assert store.list_history()[0]["inserted"] == 1
    feedback = store.list_review_feedback()[0]
    assert feedback["decision"] == "inserted"
    assert feedback["edited"] == 0


def test_review_updates_the_same_encrypted_speech_log(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    settings = store.get_settings()
    settings.review_before_insert = True
    settings.speech_logging_enabled = True
    settings.streaming_enabled = False
    store.save_settings(settings)
    engine = DictationEngine(
        store,
        recorder=FakeRecorder(),
        transcriber=FakeTranscriber("we should go. to the store"),
        correction=CorrectionPipeline(store.vocabulary_map, settings),
        inserter=FakeInserter(),
    )
    assert engine.start_recording()
    assert engine.stop_recording()
    _wait(engine)
    before = store.list_speech_improvement_logs()
    assert len(before) == 1
    assert before[0]["status"] == "review_pending"
    assert engine.complete_review("We should go to the store.", decision="accepted")
    after = store.list_speech_improvement_logs()
    assert len(after) == 1
    assert after[0]["status"] == "complete"
    assert after[0]["review_decision"] == "accepted"
    assert after[0]["analysis"]["counts"]["removed_during_review"] == 1


def test_review_offers_safe_learning_only_for_accepted_edits(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    settings = store.get_settings()
    settings.review_before_insert = True
    settings.personal_learning_enabled = True
    store.save_settings(settings)
    engine = DictationEngine(
        store,
        recorder=FakeRecorder(),
        transcriber=FakeTranscriber("send teh report"),
        correction=CorrectionPipeline(store.vocabulary_map, settings),
        inserter=FakeInserter(),
    )

    assert engine.start_recording()
    assert engine.stop_recording()
    _wait(engine)
    proposals = engine.learning_proposals_for_review(
        "Send the report.", decision="accepted"
    )
    assert [(item.source, item.replacement) for item in proposals] == [("teh", "the")]
    assert engine.learning_proposals_for_review(
        "Send the report.", decision="rejected"
    ) == ()
    assert engine.learning_proposals_for_review(
        "Send teh report.", decision="accepted"
    ) == ()
    assert store.list_learned_corrections() == []
    assert engine.complete_review("Send the report.", decision="accepted")
    assert store.list_learned_corrections() == []


def test_streaming_preview_emits_local_partial_before_final_text(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    settings = store.get_settings()
    settings.streaming_enabled = True
    settings.streaming_interval_seconds = 0.01
    store.save_settings(settings)
    events = []
    engine = DictationEngine(
        store,
        recorder=StreamingRecorder(),
        transcriber=StreamingTranscriber("hello final text"),
        correction=CorrectionPipeline(store.vocabulary_map, settings),
        inserter=FakeInserter(),
        event_callback=events.append,
    )
    assert engine.start_recording(insert_result=False)
    deadline = time.monotonic() + 1
    while not any(event.is_partial for event in events) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert any(event.is_partial for event in events)
    partial = next(event for event in events if event.is_partial)
    assert partial.raw_text == "hello live preview"
    assert partial.final_text.strip() == "Hello live preview."
    assert engine.stop_recording()
    _wait(engine)
    assert events[-1].final_text.strip() == "Hello final text."


def test_streaming_preview_commits_stable_words_and_checkpoints_recovery(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    settings = store.get_settings()
    settings.streaming_enabled = True
    settings.streaming_interval_seconds = 0.01
    settings.keep_recovery_text = True
    store.save_settings(settings)
    events = []
    engine = DictationEngine(
        store,
        recorder=StreamingRecorder(),
        transcriber=StreamingTranscriber("hello final text"),
        correction=CorrectionPipeline(store.vocabulary_map, settings),
        inserter=FakeInserter(),
        event_callback=events.append,
    )
    assert engine.start_recording(insert_result=False)
    deadline = time.monotonic() + 1
    while not any(event.stable_text for event in events) and time.monotonic() < deadline:
        time.sleep(0.01)
    stable_event = next(event for event in events if event.stable_text)
    assert stable_event.stable_text == "Hello"
    checkpoint = store.list_recovery_items()[0]
    assert checkpoint["status"] == "recording"
    assert checkpoint["final_text"].strip() == "Hello live preview."
    assert engine.stop_recording()
    _wait(engine)
    assert store.list_recovery_items() == []


def test_failed_insert_saves_usable_text_only_when_recovery_is_enabled(tmp_path):
    for enabled, expected in ((False, 0), (True, 1)):
        store = EchoLinkStore(tmp_path / f"recovery-{enabled}.db")
        settings = store.get_settings()
        settings.keep_recovery_text = enabled
        store.save_settings(settings)
        engine = DictationEngine(
            store,
            recorder=FakeRecorder(),
            transcriber=FakeTranscriber("hello recovery"),
            correction=CorrectionPipeline(store.vocabulary_map, settings),
            inserter=FailingInserter(),
        )
        assert engine.start_recording()
        assert engine.stop_recording()
        deadline = time.monotonic() + 2
        while engine.state is not SessionState.ERROR and time.monotonic() < deadline:
            time.sleep(0.01)
        assert engine.state is SessionState.ERROR
        rows = store.list_recovery_items()
        assert len(rows) == expected
        if enabled:
            assert rows[0]["final_text"].strip() == "Hello recovery."


def test_partial_insert_exposes_and_saves_only_unsent_suffix(tmp_path):
    store = EchoLinkStore(tmp_path / "partial-recovery.db")
    settings = store.get_settings()
    settings.keep_recovery_text = True
    store.save_settings(settings)
    events = []
    engine = DictationEngine(
        store,
        recorder=FakeRecorder(),
        transcriber=FakeTranscriber("hello partial"),
        correction=CorrectionPipeline(store.vocabulary_map, settings),
        inserter=PartiallyFailingInserter(),
        event_callback=events.append,
    )

    assert engine.start_recording()
    assert engine.stop_recording()
    deadline = time.monotonic() + 2
    while engine.state is not SessionState.ERROR and time.monotonic() < deadline:
        time.sleep(0.01)

    assert engine.state is SessionState.ERROR
    error = events[-1]
    assert error.partial_insertion is True
    assert error.raw_text == ""
    assert error.final_text.strip() == "partial."
    assert error.possibly_inserted_utf16_units == len("Hello ")
    assert error.total_insertion_utf16_units == len("Hello partial. ")
    assert "Hello partial" not in error.final_text
    assert "did not retry" in error.message
    rows = store.list_recovery_items()
    assert len(rows) == 1
    assert rows[0]["final_text"].strip() == "partial."
    assert "Hello partial" not in rows[0]["final_text"]


class SlowStreamingTranscriber(StreamingTranscriber):
    """A preview pass that takes far longer than the interval between passes.

    This is what a real one becomes as an utterance grows: every pass
    re-transcribes the whole recording from the start, so a thirteen second
    sentence was measured producing six passes of 2.4 seconds each.
    """

    def __init__(self, final_text, delay=0.12):
        super().__init__(final_text)
        self.delay = delay
        self.starts = []

    def transcribe_partial(self, audio):
        self.starts.append(time.monotonic())
        time.sleep(self.delay)
        return super().transcribe_partial(audio)


def test_a_preview_pass_slower_than_the_interval_backs_off(tmp_path):
    """A pass slower than the gap between passes must widen the gap.

    Every pass holds the transcription lock the final text needs, so passes
    issued as fast as the interval allows land directly on the delay the
    speaker feels after they stop talking - measured around twenty seconds on
    a long sentence.

    Measured directly rather than by counting passes. With a 0.01s interval
    and a 0.4s pass, no back-off gives roughly a 0.55s cycle and back-off
    roughly 0.95s, once the pipeline's own ~0.14s per cycle is included. A
    count-based bound could not tell those apart, and neither could a smaller
    delay - both were tried.
    """
    store = EchoLinkStore(tmp_path / "echo-link.db")
    settings = store.get_settings()
    settings.streaming_enabled = True
    settings.streaming_interval_seconds = 0.01
    store.save_settings(settings)
    transcriber = SlowStreamingTranscriber("hello final text", delay=0.4)
    engine = DictationEngine(
        store,
        recorder=StreamingRecorder(),
        transcriber=transcriber,
        correction=CorrectionPipeline(store.vocabulary_map, settings),
        inserter=FakeInserter(),
        event_callback=lambda _event: None,
    )
    assert engine.start_recording(insert_result=False)
    time.sleep(2.6)
    assert engine.stop_recording()
    _wait(engine)

    starts = transcriber.starts
    assert len(starts) >= 3, starts
    gaps = [second - first for first, second in zip(starts, starts[1:])]
    typical = sorted(gaps)[len(gaps) // 2]
    assert typical >= 0.75, gaps
