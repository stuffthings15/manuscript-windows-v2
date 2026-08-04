from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .audio import MicrophoneRecorder
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
        self.settings = store.get_settings()
        self.recorder = recorder or MicrophoneRecorder()
        self.transcriber = transcriber or LocalWhisperTranscriber(self.settings)
        self.correction = correction or CorrectionPipeline(
            store.vocabulary_map,
            self.settings,
            snippet_provider=store.snippet_map,
            learned_correction_provider=store.learned_correction_map,
        )
        self.inserter = inserter or WindowsTextInserter(self.settings.insertion_mode)
        self.event_callback = event_callback
        self.level_callback = level_callback
        self._state = SessionState.READY
        self._lock = threading.RLock()
        self._worker: threading.Thread | None = None
        self._started_at = 0.0
        self._insert_for_session = True
        self._review_for_session = False
        self._pending_review: _PendingReview | None = None
        self._target_window = active_window()
        self._target_title = ""
        self._stream_stop = threading.Event()
        self._stream_thread: threading.Thread | None = None
        self._session_id = 0
        self._recovery_session_key = ""
        self._insertion_in_progress = False
        self._recording_timer: threading.Timer | None = None
        self._hands_free_session = False
        self._silence_tracker: SilenceAutoStopTracker | None = None
        self._silence_stop = threading.Event()
        self._silence_thread: threading.Thread | None = None

    @property
    def state(self) -> SessionState:
        with self._lock:
            return self._state

    @property
    def is_busy(self) -> bool:
        return self.state in {
            SessionState.LISTENING,
            SessionState.TRANSCRIBING,
            SessionState.REFINING,
        }

    @property
    def has_pending_review(self) -> bool:
        with self._lock:
            return self._pending_review is not None

    @property
    def hands_free_session(self) -> bool:
        with self._lock:
            return self._hands_free_session and self._state is SessionState.LISTENING

    def update_settings(self, settings: DictationSettings) -> None:
        self.store.save_settings(settings)
        self.settings = settings
        self.transcriber.update_settings(settings)
        self.correction.update_settings(settings)
        if hasattr(self.inserter, "set_mode"):
            self.inserter.set_mode(settings.insertion_mode)
        self.arm_microphone()

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
            if self.is_busy or self._pending_review is not None:
                return False
            requested_insert = (
                self.settings.insert_text if insert_result is None else bool(insert_result)
            )
            self._review_for_session = bool(
                requested_insert and self.settings.review_before_insert
            )
            self._insert_for_session = bool(requested_insert and not self._review_for_session)
            self._pending_review = None
            self._target_window = target_window or active_window()
            self._target_title = self._target_window.title
            self._started_at = time.monotonic()
            self._session_id += 1
            session_id = self._session_id
            self._recovery_session_key = f"{time.time_ns()}-{session_id}"
            self._insertion_in_progress = False
            self._hands_free_session = bool(hands_free)
            try:
                self.recorder.start(
                    self.settings.microphone_index,
                    level_callback=self._emit_level,
                )
            except Exception as exc:
                self._hands_free_session = False
                try:
                    self.recorder.cancel()
                except Exception:
                    logger.warning("Microphone start cleanup failed", exc_info=True)
                self._set_state(SessionState.ERROR)
                self._emit_level(0.0)
                self._emit(SessionState.ERROR, str(exc))
                return False
            self._set_state(SessionState.LISTENING)
            self._cancel_recording_timer_locked()
            limit = max(0.0, float(self.settings.max_recording_seconds))
            if limit:
                timer = threading.Timer(limit, self._stop_at_recording_limit)
                timer.daemon = True
                self._recording_timer = timer
                timer.start()
            self._start_silence_watchdog_locked(session_id)
        message = "Hands-free listening... Press Esc to cancel." if hands_free else "Listening..."
        self._emit(SessionState.LISTENING, message)
        self._start_streaming_preview(session_id)
        return True

    def stop_recording(self, message: str = "Transcribing locally...") -> bool:
        with self._lock:
            if self._state is not SessionState.LISTENING:
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
                self._hands_free_session = False
                self._set_state(SessionState.ERROR)
                message = f"Microphone stop failed: {exc}"
                if self.settings.keep_recovery_text:
                    try:
                        self.store.mark_recovery_failed(self._recovery_session_key, message)
                    except Exception:
                        logger.warning("Could not mark local recovery text as failed", exc_info=True)
                self._emit_level(0.0)
                self._emit(SessionState.ERROR, message)
                return False
            self._set_state(SessionState.TRANSCRIBING)
            self._hands_free_session = False
            self._worker = threading.Thread(
                target=self._process,
                args=(audio,),
                daemon=True,
                name="echo-link-dictation",
            )
            self._worker.start()
        self._emit_level(0.0)
        self._emit(SessionState.TRANSCRIBING, message)
        return True

    def cancel_recording(self) -> bool:
        with self._lock:
            if self._state is not SessionState.LISTENING:
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
                self._set_state(SessionState.ERROR)
                self._hands_free_session = False
                if self.settings.keep_recovery_text:
                    try:
                        self.store.delete_recovery_session(self._recovery_session_key)
                    except Exception:
                        logger.warning("Canceled recovery checkpoint cleanup failed", exc_info=True)
                self._emit_level(0.0)
                self._emit(SessionState.ERROR, f"Microphone cancel failed: {exc}")
                return False
            self._set_state(SessionState.READY)
            self._hands_free_session = False
        try:
            self.store.delete_recovery_session(self._recovery_session_key)
        except Exception:
            logger.debug("Canceled recovery checkpoint cleanup failed", exc_info=True)
        self._emit_level(0.0)
        self._emit(SessionState.READY, "Hands-free recording canceled.")
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
        if not (
            self._hands_free_session
            and self.settings.silence_auto_stop_enabled
            and float(self.settings.silence_timeout_seconds) > 0
        ):
            return
        threshold = 0.02 if self.settings.whisper_mode else 0.04
        self._silence_tracker = SilenceAutoStopTracker(
            self.settings.silence_timeout_seconds,
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
                if session_id != self._session_id or self._state is not SessionState.LISTENING:
                    return
                tracker = self._silence_tracker
                should_stop = tracker is not None and tracker.should_stop()
            if should_stop:
                self._emit(SessionState.LISTENING, "Silence detected. Finishing hands-free dictation...")
                self.stop_recording("Silence detected. Transcribing locally...")
                return

    def toggle_recording(self, *, insert_result: bool | None = None) -> bool:
        if self.state is SessionState.LISTENING:
            return self.stop_recording()
        return self.start_recording(insert_result=insert_result)

    def _start_streaming_preview(self, session_id: int) -> None:
        if not self.settings.streaming_enabled:
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
        interval = max(0.25, float(self.settings.streaming_interval_seconds))
        last_emitted: tuple[str, str] = ("", "")
        assembler = StablePartialAssembler()
        while not stop.wait(interval):
            with self._lock:
                if session_id != self._session_id or self._state is not SessionState.LISTENING:
                    return
            try:
                audio = self.recorder.snapshot()
                partial = self.transcriber.transcribe_partial(audio)
            except Exception:
                logger.debug("Live transcription preview failed", exc_info=True)
                continue
            with self._lock:
                if session_id != self._session_id or self._state is not SessionState.LISTENING:
                    return
            if not partial:
                continue
            corrected = self.correction.deterministic(partial).text
            transcript = assembler.update(corrected)
            signature = (transcript.display_text, transcript.stable_text)
            if signature == last_emitted:
                continue
            last_emitted = signature
            if transcript.stable_text and self.settings.keep_recovery_text:
                try:
                    self.store.checkpoint_recovery(
                        self._recovery_session_key,
                        transcript.display_text,
                        self.settings.mode.value,
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

    def _process(self, audio: np.ndarray) -> None:
        raw_text = ""
        deterministic_text = ""
        final_text = ""
        try:
            raw_text = self.transcriber.transcribe(
                audio,
                mode=self.settings.mode,
                status_callback=lambda message: self._emit(
                    SessionState.TRANSCRIBING, message
                ),
            )
            deterministic = self.correction.deterministic(raw_text)
            deterministic_text = deterministic.text
            final_text = deterministic.text
            if not deterministic.text.strip():
                raise RuntimeError("No usable text was produced.")
            warning = deterministic.warning
            if self.settings.keep_recovery_text:
                try:
                    self.store.checkpoint_recovery(
                        self._recovery_session_key,
                        final_text,
                        self.settings.mode.value,
                    )
                except Exception:
                    logger.warning("Could not save local recovery checkpoint", exc_info=True)
                    warning = _join_warning(
                        warning,
                        "The local recovery checkpoint could not be saved.",
                    )

            if self.settings.mode is DictationMode.ACCURACY:
                self._set_state(SessionState.REFINING)
                self._emit(
                    SessionState.REFINING,
                    "Correcting grammar and punctuation locally...",
                    raw_text=raw_text,
                    final_text=deterministic.text,
                )
                try:
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
                if self.settings.keep_recovery_text and final_text != deterministic.text:
                    try:
                        self.store.checkpoint_recovery(
                            self._recovery_session_key,
                            final_text,
                            self.settings.mode.value,
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

            inserted, record = self._insert(deterministic.text)
            if inserted:
                self._clear_inserted_recovery_checkpoint()
            if self.settings.mode in (DictationMode.FAST, DictationMode.DEVELOPER):
                self._finish(raw_text, deterministic_text, deterministic.text, inserted, warning, audio)
                return

            self._set_state(SessionState.REFINING)
            self._emit(
                SessionState.REFINING,
                "Text inserted. Refining locally..." if inserted else "Refining locally...",
                raw_text=raw_text,
                final_text=deterministic.text,
                inserted=inserted,
            )
            final_text = deterministic.text
            try:
                refined = self.correction.correct(raw_text, DictationMode.REFINE)
                warning = _join_warning(warning, refined.warning)
            except Exception:
                logger.warning("Post-insert refinement failed; keeping deterministic text", exc_info=True)
                refined = deterministic
                warning = _join_warning(
                    warning,
                    "Local refinement failed. Deterministic text was kept.",
                )
            if refined.text != deterministic.text:
                if inserted and record is not None:
                    try:
                        replacement = self.inserter.replace_if_safe(record, refined.text)
                    except Exception:
                        logger.warning("Refined replacement failed", exc_info=True)
                        replacement = None
                    if replacement is not None:
                        final_text = refined.text
                    else:
                        warning = _join_warning(
                            warning,
                            "Refined text was ready, but replacement was skipped because focus or input changed.",
                        )
                else:
                    final_text = refined.text
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
            if recovery_text.strip() and self.settings.keep_recovery_text:
                try:
                    self.store.checkpoint_recovery(
                        self._recovery_session_key,
                        recovery_text,
                        self.settings.mode.value,
                        status="insertion_uncertain" if insertion_uncertain else "failed",
                        error_message=failure_message,
                    )
                except Exception:
                    logger.warning("Could not save local recovery text", exc_info=True)
            elif partial_insertion and self.settings.keep_recovery_text:
                try:
                    self.store.delete_recovery_session(self._recovery_session_key)
                except Exception:
                    logger.warning("Could not clear completed partial recovery", exc_info=True)
            elif self.settings.keep_recovery_text:
                try:
                    self.store.mark_recovery_failed(self._recovery_session_key, str(exc))
                except Exception:
                    logger.warning("Could not mark local recovery text as failed", exc_info=True)
            if self.settings.speech_logging_enabled and np.asarray(audio).size:
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
        if not self._insert_for_session:
            return False, None
        if self.settings.keep_recovery_text:
            try:
                self.store.checkpoint_recovery(
                    self._recovery_session_key,
                    text,
                    self.settings.mode.value,
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
        latency = self._latency_ms()
        if self.settings.keep_recovery_text:
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
        if self.settings.speech_logging_enabled:
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
                    target_window=self._target_window,
                    started_at=self._started_at,
                    mode=self.settings.mode.value,
                    speech_log_id=speech_log_id,
                )
        elif self.settings.keep_history:
            try:
                self.store.add_history(
                    raw_text,
                    final_text,
                    self.settings.mode.value,
                    window_title=self._target_title,
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
            mode=self.settings.mode.value,
            analysis=analysis,
            retention_days=self.settings.speech_log_retention_days,
            window_title=self._target_title,
            latency_ms=latency_ms,
            inserted=inserted,
            status=status,
        )

    def _clear_inserted_recovery_checkpoint(self) -> None:
        if not self.settings.keep_recovery_text:
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
        with self._lock:
            self._pending_review = None
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
        with self._lock:
            self._cancel_recording_timer_locked()
            self._cancel_silence_watchdog_locked()
        self._stream_stop.set()
        thread = self._stream_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        try:
            self.recorder.cancel()
        finally:
            self.transcriber.release_model()

    def _set_state(self, state: SessionState) -> None:
        with self._lock:
            self._state = state

    def _emit(self, state: SessionState, message: str, **values) -> None:
        if not self.event_callback:
            return
        event = SessionEvent(state=state, message=message, **values)
        try:
            self.event_callback(event)
        except Exception:
            logger.debug("Session event callback failed", exc_info=True)

    def _emit_level(self, value: float) -> None:
        normalized = max(0.0, min(1.0, float(value)))
        with self._lock:
            tracker = self._silence_tracker
            if tracker is not None and self._state is SessionState.LISTENING:
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
