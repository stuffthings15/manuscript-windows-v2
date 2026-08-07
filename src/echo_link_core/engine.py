from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .audio import MicrophoneRecorder
from .accepted_inference import (
    AcceptedInferenceCoordinator,
    AcceptedInferenceLease,
)
from .correction import CorrectionPipeline
from .insertion import (
    InsertionRecord,
    PartialTextInsertionError,
    WindowSnapshot,
    WindowsTextInserter,
    active_window,
)
from .learning import LearningProposal, propose_learning
from .models import DictationMode, DictationSettings, SessionEvent, SessionState
from .silence import SilenceAutoStopTracker
from .storage import EchoLinkStore
from .speech_logs import analyze_punctuation
from .streaming import StablePartialAssembler
from .transcription import LocalWhisperTranscriber


logger = logging.getLogger(__name__)
_LEASE_UNSET = object()

_INSERTION_UNCERTAIN_MESSAGE = (
    "Manuscript stopped while insertion was in progress. Text may already be in the target. "
    "Check the target before copying this recovery text. Manuscript will not retry it automatically."
)


@dataclass(frozen=True, slots=True)
class _PendingReview:
    raw_text: str
    deterministic_text: str
    suggested_text: str
    target_window: WindowSnapshot
    started_at: float
    mode: str
    speech_log_id: int | None = None


@dataclass(frozen=True, slots=True)
class _CaptureSession:
    session_id: int
    target_window: WindowSnapshot
    settings: DictationSettings
    started_at: float
    insert_result: bool
    review_result: bool
    recovery_session_key: str
    hands_free: bool


@dataclass(frozen=True, slots=True)
class _DictationJob:
    sequence: int
    capture: _CaptureSession
    audio: np.ndarray


class _ProcessingWorkerView:
    """Compatibility view for older reliability probes."""

    def __init__(self, engine: "DictationEngine") -> None:
        self._engine = engine

    def is_alive(self) -> bool:
        with self._engine._lock:
            return bool(
                self._engine._active_job is not None
                or self._engine._job_queue
            )

    def join(self, timeout: float | None = None) -> None:
        self._engine.wait_until_idle(2.0 if timeout is None else timeout)


class DictationEngine:
    def __init__(
        self,
        store: EchoLinkStore,
        *,
        recorder=None,
        transcriber=None,
        correction=None,
        inserter=None,
        event_callback: Callable[[SessionEvent], None] | None = None,
        level_callback: Callable[[float], None] | None = None,
    ):
        self.store = store
        loaded_settings = DictationSettings.from_dict(store.get_settings().to_dict())
        # Persisted model names are preferences from an earlier run. Each app
        # start must re-accept a model for the current hardware and runtime.
        self._accepted_ai_model = ""
        self._accepted_ai_pack_id = ""
        self._accepted_ai_policy_version = 0
        self._accepted_ai_plan = None
        self._ai_coordinator: AcceptedInferenceCoordinator | None = None
        loaded_settings.ollama_model = ""
        loaded_settings.polish_model_pack_id = ""
        self.settings = loaded_settings
        self.recorder = recorder or MicrophoneRecorder()
        self.transcriber = transcriber or LocalWhisperTranscriber(self.settings)
        self.correction = correction or CorrectionPipeline(
            store.vocabulary_map,
            self.settings,
            snippet_provider=store.snippet_map,
            learned_correction_provider=store.learned_correction_map,
        )
        self.correction.update_settings(self.settings)
        clear_accepted = getattr(self.correction, "clear_accepted_model", None)
        if callable(clear_accepted):
            clear_accepted()
        self.inserter = inserter or WindowsTextInserter(self.settings.insertion_mode)
        self.event_callback = event_callback
        self.level_callback = level_callback
        self._last_state = SessionState.READY
        self._processing_state: SessionState | None = None
        self._lock = threading.RLock()
        self._queue_condition = threading.Condition(self._lock)
        self._job_queue: deque[_DictationJob] = deque()
        self._active_job: _DictationJob | None = None
        self._processing_thread: threading.Thread | None = None
        self._shutdown_requested = False
        self._active_capture: _CaptureSession | None = None
        self._last_target_window = active_window()
        self._pending_review: _PendingReview | None = None
        self._stream_stop = threading.Event()
        self._stream_thread: threading.Thread | None = None
        self._session_id = 0
        self._job_sequence = 0
        self._recording_timer: threading.Timer | None = None
        self._silence_tracker: SilenceAutoStopTracker | None = None
        self._silence_stop = threading.Event()
        self._silence_thread: threading.Thread | None = None
        self._transcription_lock = threading.Lock()
        self._correction_lock = threading.RLock()
        self._correction_warm_thread: threading.Thread | None = None
        self._ai_plan_validator: Callable[[str, str, int, bool], bool] | None = None
        self._insertion_in_progress = False
        self._worker = _ProcessingWorkerView(self)

    @property
    def state(self) -> SessionState:
        with self._lock:
            if self._active_capture is not None:
                return SessionState.LISTENING
            return self._processing_state or self._last_state

    @property
    def is_busy(self) -> bool:
        with self._lock:
            return bool(
                self._active_capture is not None
                or self._active_job is not None
                or self._job_queue
            )

    @property
    def can_start_recording(self) -> bool:
        with self._lock:
            return bool(
                not self._shutdown_requested
                and self._active_capture is None
                and self._pending_review is None
            )

    @property
    def queued_job_count(self) -> int:
        with self._lock:
            return len(self._job_queue)

    @property
    def _recovery_session_key(self) -> str:
        with self._lock:
            if self._active_capture is not None:
                return self._active_capture.recovery_session_key
            if self._active_job is not None:
                return self._active_job.capture.recovery_session_key
            return ""

    @property
    def _target_window(self) -> WindowSnapshot:
        with self._lock:
            if self._active_capture is not None:
                return self._active_capture.target_window
            if self._active_job is not None:
                return self._active_job.capture.target_window
            return self._last_target_window

    @property
    def _target_title(self) -> str:
        return self._target_window.title

    @property
    def _started_at(self) -> float:
        with self._lock:
            if self._active_job is not None:
                return self._active_job.capture.started_at
            if self._active_capture is not None:
                return self._active_capture.started_at
            return time.monotonic()

    @property
    def _insert_for_session(self) -> bool:
        with self._lock:
            return bool(
                self._active_job is not None
                and self._active_job.capture.insert_result
            )

    @property
    def _review_for_session(self) -> bool:
        with self._lock:
            return bool(
                self._active_job is not None
                and self._active_job.capture.review_result
            )

    @property
    def has_pending_review(self) -> bool:
        with self._lock:
            return self._pending_review is not None

    @property
    def hands_free_session(self) -> bool:
        with self._lock:
            capture = self._active_capture
            return bool(capture is not None and capture.hands_free)

    def update_settings(self, settings: DictationSettings) -> None:
        safe_settings = DictationSettings.from_dict(settings.to_dict())
        with self._correction_lock:
            with self._lock:
                safe_settings.ollama_model = self._accepted_ai_model
                if self._accepted_ai_model:
                    safe_settings.polish_model_pack_id = self._accepted_ai_pack_id
                    safe_settings.polish_policy_version = (
                        self._accepted_ai_policy_version
                    )
                else:
                    safe_settings.polish_model_pack_id = ""
            self.store.save_settings(safe_settings)
            self.transcriber.update_settings(safe_settings)
            self.correction.update_settings(safe_settings)
            with self._lock:
                self.settings = safe_settings
        if hasattr(self.inserter, "set_mode"):
            self.inserter.set_mode(safe_settings.insertion_mode)
        self.arm_microphone()

    def accept_ai_plan(
        self,
        *,
        pack_id: str,
        model: str,
        policy_version: int,
    ) -> None:
        accepted_pack = str(pack_id or "").strip()
        accepted_model = str(model or "").strip()
        if bool(accepted_pack) != bool(accepted_model):
            raise ValueError("An accepted AI plan requires both pack and model identifiers.")
        coordinator = self._ai_coordinator
        if coordinator is not None:
            if not accepted_model:
                coordinator.clear()
                self.sync_ai_plan_from_coordinator(None)
                return
            lease = coordinator.lease()
            if lease is None or not coordinator.is_current(
                accepted_pack,
                accepted_model,
                policy_version,
            ):
                raise ValueError(
                    "A full accepted execution plan is required before sharing its model."
                )
            self.sync_ai_plan_from_coordinator(lease)
            return
        if accepted_model and isinstance(self.correction, CorrectionPipeline):
            raise ValueError(
                "Dictation AI requires a verified plan from the shared coordinator."
            )
        self._apply_accepted_ai_plan(
            accepted_pack,
            accepted_model,
            policy_version,
            plan=None,
        )

    def _apply_accepted_ai_plan(
        self,
        accepted_pack: str,
        accepted_model: str,
        policy_version: int,
        *,
        plan,
    ) -> None:
        with self._correction_lock:
            with self._lock:
                accepted_settings = DictationSettings.from_dict(
                    self.settings.to_dict()
                )
            accepted_settings.polish_model_pack_id = accepted_pack
            accepted_settings.ollama_model = accepted_model
            accepted_settings.polish_policy_version = max(1, int(policy_version))
            self.store.save_settings(accepted_settings)
            self.correction.update_settings(accepted_settings)
            if accepted_model:
                accept_model = getattr(self.correction, "accept_model", None)
                if callable(accept_model):
                    accept_model(accepted_model)
            else:
                clear_model = getattr(self.correction, "clear_accepted_model", None)
                if callable(clear_model):
                    clear_model()
            with self._lock:
                self._accepted_ai_model = accepted_model
                self._accepted_ai_pack_id = accepted_pack
                self._accepted_ai_policy_version = (
                    accepted_settings.polish_policy_version
                    if accepted_model
                    else 0
                )
                self._accepted_ai_plan = plan if accepted_model else None
                self.settings = accepted_settings

    def set_ai_coordinator(
        self,
        coordinator: AcceptedInferenceCoordinator,
    ) -> None:
        if not isinstance(coordinator, AcceptedInferenceCoordinator):
            raise TypeError("An accepted inference coordinator is required.")
        with self._correction_lock:
            self._ai_coordinator = coordinator
            set_refiner = getattr(self.correction, "set_refiner", None)
            if not callable(set_refiner):
                raise TypeError("The correction pipeline cannot use shared local AI.")
            set_refiner(coordinator)
        coordinator.add_listener(self.sync_ai_plan_from_coordinator)
        self.sync_ai_plan_from_coordinator(coordinator.lease())

    def sync_ai_plan_from_coordinator(
        self,
        lease: AcceptedInferenceLease | None | object = _LEASE_UNSET,
    ) -> None:
        coordinator = self._ai_coordinator
        if lease is _LEASE_UNSET:
            lease = coordinator.lease() if coordinator is not None else None
        if isinstance(lease, AcceptedInferenceLease):
            plan = lease.plan
            self._apply_accepted_ai_plan(
                plan.pack_id,
                plan.model,
                plan.policy_version,
                plan=plan,
            )
        else:
            self._apply_accepted_ai_plan("", "", 1, plan=None)

    def set_ai_plan_validator(
        self,
        validator: Callable[[str, str, int, bool], bool] | None,
    ) -> None:
        with self._lock:
            self._ai_plan_validator = validator

    def _validate_accepted_ai_plan(self, *, require_loaded: bool = False) -> bool:
        with self._correction_lock:
            with self._lock:
                model = self._accepted_ai_model
                pack_id = self._accepted_ai_pack_id
                policy_version = self._accepted_ai_policy_version
                validator = self._ai_plan_validator
                coordinator = self._ai_coordinator
            if not model or not pack_id:
                return False
            if coordinator is None and isinstance(
                self.correction,
                CorrectionPipeline,
            ):
                self._apply_accepted_ai_plan("", "", 1, plan=None)
                return False
            if coordinator is not None and not coordinator.is_current(
                pack_id,
                model,
                policy_version,
                require_loaded=require_loaded,
            ):
                self.accept_ai_plan(
                    pack_id="",
                    model="",
                    policy_version=max(1, policy_version),
                )
                return False
            if validator is None:
                return True
            try:
                valid = bool(
                    validator(pack_id, model, policy_version, require_loaded)
                )
            except Exception:
                logger.warning("Accepted AI plan revalidation failed", exc_info=True)
                valid = False
            if valid:
                return True
            self.accept_ai_plan(
                pack_id="",
                model="",
                policy_version=max(1, policy_version),
            )
            return False

    def prepare_ai_for_execution(
        self,
        *,
        settings: DictationSettings | None = None,
        timeout: float | None = None,
    ) -> bool:
        if not self._validate_accepted_ai_plan():
            return False
        active_settings = settings or self.settings
        preload = getattr(self.correction, "preload", None)
        if not callable(preload):
            return False
        try:
            with self._correction_lock:
                self.correction.update_settings(active_settings)
                preload(
                    timeout=max(
                        30.0,
                        float(timeout or active_settings.correction_timeout_seconds),
                    )
                )
        except Exception:
            logger.warning("Accepted AI model preload failed", exc_info=True)
            self.accept_ai_plan(
                pack_id="",
                model="",
                policy_version=max(1, self.settings.polish_policy_version),
            )
            return False
        return self._validate_accepted_ai_plan(require_loaded=True)

    def arm_microphone(self) -> bool:
        if not hasattr(self.recorder, "arm"):
            return False
        try:
            self.recorder.arm(self.settings.microphone_index, self.settings.pre_roll_ms)
            return self.settings.pre_roll_ms > 0
        except Exception:
            logger.warning("Microphone pre-roll could not be started", exc_info=True)
            return False

    def start_recording(
        self,
        *,
        insert_result: bool | None = None,
        target_window=None,
        hands_free: bool = False,
    ) -> bool:
        with self._lock:
            if not self.can_start_recording:
                return False
            requested_insert = (
                self.settings.insert_text if insert_result is None else bool(insert_result)
            )
            review_result = bool(
                requested_insert and self.settings.review_before_insert
            )
            insert_for_session = bool(requested_insert and not review_result)
            captured_target = target_window or active_window()
            settings_snapshot = DictationSettings.from_dict(self.settings.to_dict())
            started_at = time.monotonic()
            self._session_id += 1
            session_id = self._session_id
            capture = _CaptureSession(
                session_id=session_id,
                target_window=captured_target,
                settings=settings_snapshot,
                started_at=started_at,
                insert_result=insert_for_session,
                review_result=review_result,
                recovery_session_key=f"{time.time_ns()}-{session_id}",
                hands_free=bool(hands_free),
            )
            try:
                self.recorder.start(
                    settings_snapshot.microphone_index,
                    level_callback=self._emit_level,
                )
            except Exception as exc:
                try:
                    self.recorder.cancel()
                except Exception:
                    logger.warning("Microphone start cleanup failed", exc_info=True)
                self._last_state = SessionState.ERROR
                self._emit_level(0.0)
                self._emit(SessionState.ERROR, str(exc))
                return False
            self._active_capture = capture
            self._last_target_window = captured_target
            self._last_state = SessionState.LISTENING
            self._cancel_recording_timer_locked()
            limit = max(0.0, float(settings_snapshot.max_recording_seconds))
            if limit:
                timer = threading.Timer(limit, self._stop_at_recording_limit)
                timer.daemon = True
                self._recording_timer = timer
                timer.start()
            self._start_silence_watchdog_locked(session_id)
        message = "Hands-free listening... Press Esc to cancel." if hands_free else "Listening..."
        self._emit(
            SessionState.LISTENING,
            message,
            capture_id=session_id,
            capture_started=True,
        )
        self._start_correction_preload(settings_snapshot)
        self._start_streaming_preview(session_id)
        return True

    def _start_correction_preload(self, settings: DictationSettings) -> None:
        if (
            settings.mode in (DictationMode.FAST, DictationMode.DEVELOPER)
            or not settings.local_refinement_enabled
            or not callable(getattr(self.correction, "preload", None))
        ):
            return
        with self._lock:
            if (
                self._correction_warm_thread is not None
                and self._correction_warm_thread.is_alive()
            ):
                return
            thread = threading.Thread(
                target=self._run_correction_preload,
                args=(settings,),
                daemon=True,
                name="manuscript-dictation-ai-preload",
            )
            self._correction_warm_thread = thread
            thread.start()

    def _run_correction_preload(self, settings: DictationSettings) -> None:
        started = time.monotonic()
        try:
            if not self.prepare_ai_for_execution(settings=settings):
                return
            logger.info(
                "Dictation AI preload completed in %.3f seconds",
                time.monotonic() - started,
            )
        except Exception:
            logger.warning("Dictation AI preload failed", exc_info=True)

    def stop_recording(self, message: str = "Transcribing locally...") -> bool:
        with self._lock:
            capture = self._active_capture
            if capture is None:
                return False
            self._cancel_recording_timer_locked()
            self._cancel_silence_watchdog_locked()
            self._stream_stop.set()
            try:
                audio = self.recorder.stop()
            except Exception as exc:
                try:
                    self.recorder.cancel()
                except Exception:
                    logger.warning("Microphone stop cleanup failed", exc_info=True)
                self._active_capture = None
                self._set_state(SessionState.ERROR)
                message = f"Microphone stop failed: {exc}"
                if capture.settings.keep_recovery_text:
                    try:
                        self.store.mark_recovery_failed(capture.recovery_session_key, message)
                    except Exception:
                        logger.warning("Could not mark local recovery text as failed", exc_info=True)
                self._emit_level(0.0)
                self._emit(
                    SessionState.ERROR,
                    message,
                    capture_id=capture.session_id,
                    capture_stopped=True,
                )
                return False
            self._job_sequence += 1
            job = _DictationJob(
                sequence=self._job_sequence,
                capture=capture,
                audio=np.asarray(audio).copy(),
            )
            self._job_queue.append(job)
            self._active_capture = None
            self._last_state = SessionState.READY
        self._emit_level(0.0)
        self._emit(
            SessionState.TRANSCRIBING,
            self._queue_status(
                message.rstrip("."),
                job.sequence,
                current_is_queued=True,
            ),
            capture_id=capture.session_id,
            job_sequence=job.sequence,
            capture_stopped=True,
        )
        with self._queue_condition:
            self._ensure_processing_thread_locked()
            self._queue_condition.notify_all()
        return True

    def cancel_recording(self) -> bool:
        with self._lock:
            capture = self._active_capture
            if capture is None:
                return False
            self._cancel_recording_timer_locked()
            self._cancel_silence_watchdog_locked()
            self._stream_stop.set()
            try:
                self.recorder.stop()
            except Exception as exc:
                try:
                    self.recorder.cancel()
                except Exception:
                    logger.warning("Microphone cancel cleanup failed", exc_info=True)
                self._active_capture = None
                self._last_state = SessionState.ERROR
                if capture.settings.keep_recovery_text:
                    try:
                        self.store.delete_recovery_session(capture.recovery_session_key)
                    except Exception:
                        logger.warning("Canceled recovery checkpoint cleanup failed", exc_info=True)
                self._emit_level(0.0)
                self._emit(
                    SessionState.ERROR,
                    f"Microphone cancel failed: {exc}",
                    capture_id=capture.session_id,
                    capture_stopped=True,
                )
                return False
            self._active_capture = None
            self._last_state = SessionState.READY
        try:
            self.store.delete_recovery_session(capture.recovery_session_key)
        except Exception:
            logger.debug("Canceled recovery checkpoint cleanup failed", exc_info=True)
        self._emit_level(0.0)
        self._emit(
            SessionState.READY,
            "Hands-free recording canceled.",
            capture_id=capture.session_id,
            capture_stopped=True,
        )
        return True

    def _stop_at_recording_limit(self) -> None:
        if self.stop_recording("Recording limit reached. Transcribing locally..."):
            logger.info("Recording stopped at the configured %.1f second limit", float(self.settings.max_recording_seconds))

    def _cancel_recording_timer_locked(self) -> None:
        timer = self._recording_timer
        self._recording_timer = None
        if timer is not None:
            timer.cancel()

    def _start_silence_watchdog_locked(self, session_id: int) -> None:
        self._cancel_silence_watchdog_locked()
        capture = self._active_capture
        if not (
            capture is not None
            and capture.hands_free
            and capture.settings.silence_auto_stop_enabled
            and float(capture.settings.silence_timeout_seconds) > 0
        ):
            return
        threshold = 0.02 if capture.settings.whisper_mode else 0.04
        self._silence_tracker = SilenceAutoStopTracker(
            capture.settings.silence_timeout_seconds,
            voice_threshold=threshold,
        )
        stop = threading.Event()
        self._silence_stop = stop
        thread = threading.Thread(
            target=self._run_silence_watchdog,
            args=(session_id, stop),
            daemon=True,
            name="echo-link-silence-stop",
        )
        self._silence_thread = thread
        thread.start()

    def _cancel_silence_watchdog_locked(self) -> None:
        self._silence_stop.set()
        self._silence_tracker = None

    def _run_silence_watchdog(self, session_id: int, stop: threading.Event) -> None:
        while not stop.wait(0.1):
            with self._lock:
                capture = self._active_capture
                if capture is None or session_id != capture.session_id:
                    return
                tracker = self._silence_tracker
                should_stop = tracker is not None and tracker.should_stop()
            if should_stop:
                self._emit(SessionState.LISTENING, "Silence detected. Finishing hands-free dictation...")
                self.stop_recording("Silence detected. Transcribing locally...")
                return

    def toggle_recording(self, *, insert_result: bool | None = None) -> bool:
        with self._lock:
            recording = self._active_capture is not None
        if recording:
            return self.stop_recording()
        return self.start_recording(insert_result=insert_result)

    def _start_streaming_preview(self, session_id: int) -> None:
        with self._lock:
            capture = self._active_capture
        if capture is None or not capture.settings.streaming_enabled:
            return
        if not hasattr(self.recorder, "snapshot") or not hasattr(self.transcriber, "transcribe_partial"):
            return
        stop = threading.Event()
        self._stream_stop = stop
        thread = threading.Thread(
            target=self._run_streaming_preview,
            args=(session_id, stop),
            daemon=True,
            name="echo-link-live-preview",
        )
        self._stream_thread = thread
        thread.start()

    def _run_streaming_preview(self, session_id: int, stop: threading.Event) -> None:
        with self._lock:
            capture = self._active_capture
        if capture is None:
            return
        interval = max(0.25, float(capture.settings.streaming_interval_seconds))
        last_emitted: tuple[str, str] = ("", "")
        assembler = StablePartialAssembler()
        while not stop.wait(interval):
            with self._lock:
                capture = self._active_capture
                if capture is None or session_id != capture.session_id:
                    return
            try:
                audio = self.recorder.snapshot()
                with self._transcription_lock:
                    partial = self.transcriber.transcribe_partial(audio)
            except Exception:
                logger.debug("Live transcription preview failed", exc_info=True)
                continue
            with self._lock:
                capture = self._active_capture
                if capture is None or session_id != capture.session_id:
                    return
            if not partial:
                continue
            with self._correction_lock:
                self.correction.update_settings(capture.settings)
                corrected = self.correction.deterministic(partial).text
            transcript = assembler.update(corrected)
            signature = (transcript.display_text, transcript.stable_text)
            if signature == last_emitted:
                continue
            last_emitted = signature
            if transcript.stable_text and capture.settings.keep_recovery_text:
                try:
                    self.store.checkpoint_recovery(
                        capture.recovery_session_key,
                        transcript.display_text,
                        capture.settings.mode.value,
                    )
                except Exception:
                    logger.debug("Recovery checkpoint failed", exc_info=True)
            self._emit(
                SessionState.LISTENING,
                (
                    f"Listening... {len(transcript.stable_text.split())} stable words."
                    if transcript.stable_text
                    else "Listening... Live local preview."
                ),
                raw_text=partial,
                final_text=transcript.display_text,
                is_partial=True,
                stable_text=transcript.stable_text,
            )

    def _ensure_processing_thread_locked(self) -> None:
        thread = self._processing_thread
        if self._shutdown_requested or (thread is not None and thread.is_alive()):
            return
        thread = threading.Thread(
            target=self._run_processing_loop,
            daemon=True,
            name="manuscript-ai-correction-queue",
        )
        self._processing_thread = thread
        thread.start()

    def _run_processing_loop(self) -> None:
        while True:
            with self._queue_condition:
                if self._shutdown_requested:
                    self._processing_thread = None
                    self._queue_condition.notify_all()
                    return
                if not self._job_queue or self._pending_review is not None:
                    self._processing_thread = None
                    self._queue_condition.notify_all()
                    return
                job = self._job_queue.popleft()
                self._active_job = job
                self._processing_state = SessionState.TRANSCRIBING
            try:
                self._process(job.audio)
            finally:
                with self._queue_condition:
                    self._active_job = None
                    self._processing_state = None
                    self._queue_condition.notify_all()

    def _queue_status(
        self,
        prefix: str,
        sequence: int | None = None,
        *,
        current_is_queued: bool = False,
    ) -> str:
        with self._lock:
            current = sequence or (
                self._active_job.sequence if self._active_job is not None else 0
            )
            queued = len(self._job_queue)
            if current_is_queued and queued:
                queued -= 1
        return f"{prefix}. AI processing {current}. {queued} queued."

    def wait_until_idle(self, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._queue_condition:
            while (
                self._active_capture is not None
                or self._active_job is not None
                or self._job_queue
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._queue_condition.wait(min(0.1, remaining))
            return True

    def _process(self, audio: np.ndarray) -> None:
        raw_text = ""
        deterministic_text = ""
        final_text = ""
        with self._lock:
            job = self._active_job
        if job is None:
            return
        settings = job.capture.settings
        try:
            with self._transcription_lock:
                self.transcriber.update_settings(settings)
                raw_text = self.transcriber.transcribe(
                    audio,
                    mode=settings.mode,
                    status_callback=lambda message: self._emit(
                        SessionState.TRANSCRIBING,
                        self._queue_status(message.rstrip("."), job.sequence),
                    ),
                )
            with self._correction_lock:
                self.correction.update_settings(settings)
                deterministic = self.correction.deterministic(raw_text)
            deterministic_text = deterministic.text
            final_text = deterministic.text
            if not deterministic.text.strip():
                raise RuntimeError("No usable text was produced.")
            warning = deterministic.warning
            ai_ready = True
            if settings.mode not in (DictationMode.FAST, DictationMode.DEVELOPER):
                ai_ready = self.prepare_ai_for_execution(settings=settings)
            if settings.keep_recovery_text:
                try:
                    self.store.checkpoint_recovery(
                        self._recovery_session_key,
                        final_text,
                        settings.mode.value,
                    )
                except Exception:
                    logger.warning("Could not save local recovery checkpoint", exc_info=True)
                    warning = _join_warning(
                        warning,
                        "The local recovery checkpoint could not be saved.",
                    )

            if settings.mode is DictationMode.ACCURACY:
                self._set_state(SessionState.REFINING)
                self._emit(
                    SessionState.REFINING,
                    "Correcting grammar and punctuation locally...",
                    raw_text=raw_text,
                    final_text=deterministic.text,
                )
                try:
                    if not ai_ready:
                        raise RuntimeError("Accepted local AI is not ready.")
                    with self._correction_lock:
                        self.correction.update_settings(settings)
                        result = self.correction.correct(raw_text, DictationMode.ACCURACY)
                    final_text = result.text
                    warning = _join_warning(warning, result.warning)
                except Exception:
                    logger.warning("Accuracy refinement failed; using deterministic text", exc_info=True)
                    final_text = deterministic.text
                    warning = _join_warning(
                        warning,
                        "Local refinement failed. Deterministic text was kept.",
                    )
                if settings.keep_recovery_text and final_text != deterministic.text:
                    try:
                        self.store.checkpoint_recovery(
                            self._recovery_session_key,
                            final_text,
                            settings.mode.value,
                        )
                    except Exception:
                        logger.warning("Could not update local recovery checkpoint", exc_info=True)
                        warning = _join_warning(
                            warning,
                            "The local recovery checkpoint could not be updated.",
                        )
                inserted, _record = self._insert(final_text)
                if inserted:
                    self._clear_inserted_recovery_checkpoint()
                self._finish(raw_text, deterministic_text, final_text, inserted, warning, audio)
                return

            if settings.mode in (DictationMode.FAST, DictationMode.DEVELOPER):
                inserted, _record = self._insert(deterministic.text)
                if inserted:
                    self._clear_inserted_recovery_checkpoint()
                self._finish(raw_text, deterministic_text, deterministic.text, inserted, warning, audio)
                return

            self._set_state(SessionState.REFINING)
            self._emit(
                SessionState.REFINING,
                self._queue_status("AI correcting", job.sequence),
                raw_text=raw_text,
                final_text=deterministic.text,
                inserted=False,
            )
            final_text = deterministic.text
            try:
                if not ai_ready:
                    raise RuntimeError("Accepted local AI is not ready.")
                with self._correction_lock:
                    self.correction.update_settings(settings)
                    refined = self.correction.correct(raw_text, DictationMode.REFINE)
                warning = _join_warning(warning, refined.warning)
            except Exception:
                logger.warning("Post-insert refinement failed; keeping deterministic text", exc_info=True)
                refined = deterministic
                warning = _join_warning(
                    warning,
                    "Local refinement failed. Deterministic text was kept.",
                )
            final_text = refined.text
            inserted, _record = self._insert(final_text)
            if inserted:
                self._clear_inserted_recovery_checkpoint()
            self._finish(raw_text, deterministic_text, final_text, inserted, warning, audio)
        except Exception as exc:
            logger.exception("Dictation session failed")
            partial_insertion = isinstance(exc, PartialTextInsertionError)
            insertion_uncertain = bool(self._insertion_in_progress and not partial_insertion)
            failure_message = (
                _join_warning(str(exc), _INSERTION_UNCERTAIN_MESSAGE)
                if insertion_uncertain
                else str(exc)
            )
            recovery_text = exc.remaining_text if partial_insertion else final_text
            if recovery_text.strip() and settings.keep_recovery_text:
                try:
                    self.store.checkpoint_recovery(
                        self._recovery_session_key,
                        recovery_text,
                        settings.mode.value,
                        status="insertion_uncertain" if insertion_uncertain else "failed",
                        error_message=failure_message,
                    )
                except Exception:
                    logger.warning("Could not save local recovery text", exc_info=True)
            elif partial_insertion and settings.keep_recovery_text:
                try:
                    self.store.delete_recovery_session(self._recovery_session_key)
                except Exception:
                    logger.warning("Could not clear completed partial recovery", exc_info=True)
            elif settings.keep_recovery_text:
                try:
                    self.store.mark_recovery_failed(self._recovery_session_key, str(exc))
                except Exception:
                    logger.warning("Could not mark local recovery text as failed", exc_info=True)
            if settings.speech_logging_enabled and np.asarray(audio).size:
                try:
                    self._save_speech_log(
                        raw_text=raw_text,
                        deterministic_text=deterministic_text,
                        suggested_text=final_text,
                        final_text=recovery_text,
                        audio=audio,
                        latency_ms=self._latency_ms(),
                        inserted=False,
                        status="error",
                    )
                except Exception:
                    logger.warning("Could not save encrypted failed speech log", exc_info=True)
            self._insertion_in_progress = False
            self._set_state(SessionState.ERROR)
            self._emit(
                SessionState.ERROR,
                failure_message,
                raw_text="" if partial_insertion else raw_text,
                final_text=recovery_text,
                latency_ms=self._latency_ms(),
                partial_insertion=partial_insertion,
                possibly_inserted_utf16_units=(
                    exc.possibly_inserted_utf16_units if partial_insertion else 0
                ),
                total_insertion_utf16_units=(
                    exc.total_utf16_units if partial_insertion else 0
                ),
                insertion_uncertain=insertion_uncertain,
            )

    def _insert(self, text: str) -> tuple[bool, InsertionRecord | None]:
        with self._lock:
            job = self._active_job
        if job is None:
            raise RuntimeError("Dictation job ended before insertion.")
        settings = job.capture.settings
        if not self._insert_for_session:
            return False, None
        if settings.keep_recovery_text:
            try:
                self.store.checkpoint_recovery(
                    self._recovery_session_key,
                    text,
                    settings.mode.value,
                    status="insertion_uncertain",
                    error_message=_INSERTION_UNCERTAIN_MESSAGE,
                )
            except Exception:
                logger.warning("Could not record insertion uncertainty", exc_info=True)
        self._insertion_in_progress = True
        record = self.inserter.insert_into(self._target_window, text)
        if record is None:
            self._insertion_in_progress = False
            raise RuntimeError(
                "Manuscript did not insert text because the captured target was unavailable, "
                "focus changed, or Windows blocked the access boundary. The text remains "
                "available in Manuscript to copy or recover."
            )
        self._insertion_in_progress = False
        self._set_state(SessionState.INSERTED)
        self._emit(
            SessionState.INSERTED,
            "Text inserted.",
            final_text=text,
            inserted=True,
        )
        return True, record

    def _finish(
        self,
        raw_text: str,
        deterministic_text: str,
        final_text: str,
        inserted: bool,
        warning: str,
        audio: np.ndarray,
    ) -> None:
        with self._lock:
            job = self._active_job
        if job is None:
            raise RuntimeError("Dictation job ended before completion.")
        settings = job.capture.settings
        latency = self._latency_ms()
        if settings.keep_recovery_text:
            try:
                self.store.delete_recovery_session(self._recovery_session_key)
            except Exception:
                logger.warning("Could not clear local recovery checkpoint", exc_info=True)
                warning = _join_warning(
                    warning,
                    "Dictation finished, but its local recovery checkpoint could not be cleared.",
                )
        review_pending = bool(self._review_for_session and not inserted)
        speech_log_id = None
        if settings.speech_logging_enabled:
            try:
                speech_log_id = self._save_speech_log(
                    raw_text=raw_text,
                    deterministic_text=deterministic_text,
                    suggested_text=final_text,
                    final_text=final_text,
                    audio=audio,
                    latency_ms=latency,
                    inserted=inserted,
                    status="review_pending" if review_pending else "complete",
                )
            except Exception:
                logger.warning("Could not save encrypted speech improvement log", exc_info=True)
                warning = _join_warning(
                    warning,
                    "Dictation finished, but its encrypted speech log could not be saved.",
                )
        if review_pending:
            with self._lock:
                self._pending_review = _PendingReview(
                    raw_text=raw_text,
                    deterministic_text=deterministic_text,
                    suggested_text=final_text,
                    target_window=job.capture.target_window,
                    started_at=job.capture.started_at,
                    mode=settings.mode.value,
                    speech_log_id=speech_log_id,
                )
        elif settings.keep_history:
            try:
                self.store.add_history(
                    raw_text,
                    final_text,
                    settings.mode.value,
                    window_title=job.capture.target_window.title,
                    latency_ms=latency,
                    inserted=inserted,
                )
            except Exception:
                logger.warning("Could not save local dictation history", exc_info=True)
                warning = _join_warning(
                    warning,
                    "Dictation finished, but local history could not be saved.",
                )
        self._set_state(SessionState.COMPLETE)
        self._emit(
            SessionState.COMPLETE,
            "Review the text before inserting it." if review_pending else "Ready for the next dictation.",
            raw_text=raw_text,
            final_text=final_text,
            latency_ms=latency,
            inserted=inserted,
            warning=warning,
            review_pending=review_pending,
        )

    def _save_speech_log(
        self,
        *,
        raw_text: str,
        deterministic_text: str,
        suggested_text: str,
        final_text: str,
        audio: np.ndarray,
        latency_ms: int,
        inserted: bool,
        status: str,
    ) -> int:
        with self._lock:
            job = self._active_job
        if job is None:
            raise RuntimeError("Dictation job ended before speech logging.")
        settings = job.capture.settings
        pcm = np.asarray(audio, dtype=np.int16).reshape(-1)
        analysis = analyze_punctuation(
            raw_text,
            deterministic_text,
            suggested_text,
            final_text,
        )
        return self.store.add_speech_improvement_log(
            raw_text=raw_text,
            deterministic_text=deterministic_text,
            suggested_text=suggested_text,
            final_text=final_text,
            audio_pcm=pcm.tobytes(),
            sample_rate=int(getattr(self.recorder, "sample_rate", 16000)),
            mode=settings.mode.value,
            analysis=analysis,
            retention_days=settings.speech_log_retention_days,
            window_title=job.capture.target_window.title,
            latency_ms=latency_ms,
            inserted=inserted,
            status=status,
        )

    def _clear_inserted_recovery_checkpoint(self) -> None:
        with self._lock:
            job = self._active_job
        if job is None or not job.capture.settings.keep_recovery_text:
            return
        try:
            self.store.delete_recovery_session(self._recovery_session_key)
        except Exception:
            logger.warning("Could not immediately clear inserted recovery text", exc_info=True)

    def complete_review(self, final_text: str, *, decision: str) -> bool:
        with self._lock:
            candidate = self._pending_review
        if candidate is None:
            return False
        text = (final_text or "").strip()
        if decision in {"accepted", "copied", "inserted"} and not text:
            raise ValueError("Reviewed text cannot be empty.")

        inserted = False
        if decision == "inserted":
            record = self.inserter.insert_into(candidate.target_window, text)
            if record is None:
                raise RuntimeError("Manuscript could not return to the original app for insertion.")
            inserted = True

        latency = max(0, int((time.monotonic() - candidate.started_at) * 1000))
        edited = text.strip() != candidate.suggested_text.strip()
        self.store.add_review_feedback(
            decision,
            edited=edited,
            mode=candidate.mode,
            latency_ms=latency,
        )
        if self.settings.keep_history and decision in {"accepted", "copied", "inserted"}:
            self.store.add_history(
                candidate.raw_text,
                text,
                candidate.mode,
                window_title=candidate.target_window.title,
                latency_ms=latency,
                inserted=inserted,
            )
        if candidate.speech_log_id is not None:
            try:
                analysis = analyze_punctuation(
                    candidate.raw_text,
                    candidate.deterministic_text,
                    candidate.suggested_text,
                    text or candidate.suggested_text,
                )
                self.store.update_speech_improvement_log(
                    candidate.speech_log_id,
                    final_text=text or candidate.suggested_text,
                    review_decision=decision,
                    status="complete",
                    analysis=analysis,
                )
            except Exception:
                logger.warning("Could not update encrypted speech log review", exc_info=True)
        with self._queue_condition:
            self._pending_review = None
            self._ensure_processing_thread_locked()
            self._queue_condition.notify_all()
        self._emit(
            SessionState.COMPLETE,
            {
                "accepted": "Review accepted.",
                "copied": "Review accepted and copied.",
                "inserted": "Review accepted and inserted.",
                "rejected": "Review rejected.",
                "retry": "Review marked for retry.",
            }[decision],
            raw_text=candidate.raw_text,
            final_text=text or candidate.suggested_text,
            latency_ms=latency,
            inserted=inserted,
            review_pending=False,
        )
        return True

    def learning_proposals_for_review(
        self,
        final_text: str,
        *,
        decision: str,
    ) -> tuple[LearningProposal, ...]:
        if not self.settings.personal_learning_enabled:
            return ()
        if decision not in {"accepted", "copied", "inserted"}:
            return ()
        with self._lock:
            candidate = self._pending_review
        if candidate is None:
            return ()
        accepted = (final_text or "").strip()
        if not accepted or accepted == candidate.suggested_text.strip():
            return ()
        return propose_learning(candidate.suggested_text, accepted)

    def retry_review(self) -> bool:
        with self._lock:
            candidate = self._pending_review
        if candidate is None:
            return False
        self.complete_review(candidate.suggested_text, decision="retry")
        return self.start_recording(target_window=candidate.target_window)

    def retry_last_recording(self) -> bool:
        if self.has_pending_review:
            return self.retry_review()
        if self.state is not SessionState.ERROR:
            return False
        return self.start_recording(target_window=self._target_window)

    def undo_last(self) -> bool:
        try:
            return bool(self.inserter.undo_last())
        except Exception:
            logger.warning("Undo failed", exc_info=True)
            return False

    def shutdown(self) -> None:
        with self._queue_condition:
            self._cancel_recording_timer_locked()
            self._cancel_silence_watchdog_locked()
            self._shutdown_requested = True
            self._job_queue.clear()
            processing_thread = self._processing_thread
            self._queue_condition.notify_all()
        self._stream_stop.set()
        thread = self._stream_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        warm_thread = self._correction_warm_thread
        if warm_thread is not None and warm_thread.is_alive():
            warm_thread.join(timeout=2.0)
        if processing_thread is not None and processing_thread.is_alive():
            processing_thread.join(timeout=2.0)
        try:
            self.recorder.cancel()
        finally:
            if processing_thread is None or not processing_thread.is_alive():
                self.transcriber.release_model()

    def _set_state(self, state: SessionState) -> None:
        with self._lock:
            if self._active_job is not None:
                self._processing_state = state
            if self._active_job is None or state in {
                SessionState.COMPLETE,
                SessionState.ERROR,
            }:
                self._last_state = state

    def _emit(self, state: SessionState, message: str, **values) -> None:
        if not self.event_callback:
            return
        with self._lock:
            capture = self._active_capture
            job = self._active_job
        if capture is not None and not values.get("capture_id"):
            values["capture_id"] = capture.session_id
        if job is not None and not values.get("job_sequence"):
            values["job_sequence"] = job.sequence
        event = SessionEvent(state=state, message=message, **values)
        try:
            self.event_callback(event)
        except Exception:
            logger.debug("Session event callback failed", exc_info=True)

    def _emit_level(self, value: float) -> None:
        normalized = max(0.0, min(1.0, float(value)))
        with self._lock:
            tracker = self._silence_tracker
            if tracker is not None and self._active_capture is not None:
                tracker.observe(normalized)
        if self.level_callback:
            try:
                self.level_callback(normalized)
            except Exception:
                logger.debug("Level callback failed", exc_info=True)

    def _latency_ms(self) -> int:
        return max(0, int((time.monotonic() - self._started_at) * 1000))


def _join_warning(first: str, second: str) -> str:
    return " ".join(value for value in (first.strip(), second.strip()) if value)
