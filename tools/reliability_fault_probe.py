from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from echo_link_core.correction import CorrectionResult
from echo_link_core.engine import DictationEngine
from echo_link_core.insertion import WindowSnapshot
from echo_link_core.models import DictationMode, SessionState
from echo_link_core.storage import EchoLinkStore


AUDIO_SUFFIXES = {
    ".aac",
    ".flac",
    ".m4a",
    ".mp3",
    ".ogg",
    ".opus",
    ".pcm",
    ".raw",
    ".wav",
    ".wma",
}


class FaultRecorder:
    def __init__(self) -> None:
        self.started = False
        self.fail_start = False
        self.fail_stop = False
        self.cancel_calls = 0

    def start(self, *_args, **_kwargs) -> None:
        self.started = True
        if self.fail_start:
            raise RuntimeError("injected microphone start failure")

    def stop(self) -> np.ndarray:
        if self.fail_stop:
            raise RuntimeError("injected microphone stop failure")
        self.started = False
        return np.ones(8000, dtype=np.int16)

    def cancel(self) -> None:
        self.cancel_calls += 1
        self.started = False


class FaultTranscriber:
    def __init__(self) -> None:
        self.text = "local fault probe text"
        self.fail = False

    def transcribe(self, *_args, **_kwargs) -> str:
        if self.fail:
            raise RuntimeError("injected transcription failure")
        return self.text

    def update_settings(self, _settings) -> None:
        pass

    def release_model(self) -> None:
        pass


class FaultCorrection:
    def __init__(self) -> None:
        self.fail_deterministic = False
        self.fail_refinement = False

    def deterministic(self, raw_text: str, *_args, **_kwargs) -> CorrectionResult:
        if self.fail_deterministic:
            raise RuntimeError("injected deterministic correction failure")
        text = "Deterministic local text. " if raw_text.strip() else ""
        return CorrectionResult(text, "deterministic")

    def correct(self, _raw_text: str, _mode: DictationMode, **_kwargs) -> CorrectionResult:
        if self.fail_refinement:
            raise RuntimeError("injected local refinement failure")
        return CorrectionResult("Refined local text. ", "ollama")

    def update_settings(self, _settings) -> None:
        pass


class FaultInserter:
    def __init__(self) -> None:
        self.fail_insert = False
        self.values: list[str] = []

    def insert_into(self, _window, text: str):
        if self.fail_insert:
            raise RuntimeError("injected insertion failure")
        self.values.append(text)
        return object()

    def replace_if_safe(self, _record, text: str):
        self.values.append(text)
        return object()

    def undo_last(self) -> bool:
        return False


class FaultStore(EchoLinkStore):
    def __init__(self, path: str | Path) -> None:
        self.fail_checkpoint = False
        self.fail_mark = False
        self.fail_delete = False
        self.delete_calls = 0
        super().__init__(path)

    def checkpoint_recovery(self, *args, **kwargs) -> int:
        if self.fail_checkpoint:
            raise RuntimeError("injected recovery checkpoint failure")
        return super().checkpoint_recovery(*args, **kwargs)

    def mark_recovery_failed(self, *args, **kwargs) -> None:
        if self.fail_mark:
            raise RuntimeError("injected recovery status failure")
        super().mark_recovery_failed(*args, **kwargs)

    def delete_recovery_session(self, *args, **kwargs) -> None:
        self.delete_calls += 1
        if self.fail_delete:
            raise RuntimeError("injected recovery cleanup failure")
        super().delete_recovery_session(*args, **kwargs)


def _make_engine(directory: Path, *, mode: DictationMode = DictationMode.FAST):
    directory.mkdir(parents=True, exist_ok=True)
    store = FaultStore(directory / "echo-link.db")
    settings = store.get_settings()
    settings.keep_history = False
    settings.keep_recovery_text = True
    settings.streaming_enabled = False
    settings.silence_auto_stop_enabled = False
    settings.max_recording_seconds = 0
    settings.mode = mode
    store.save_settings(settings)
    recorder = FaultRecorder()
    transcriber = FaultTranscriber()
    correction = FaultCorrection()
    inserter = FaultInserter()
    events = []
    engine = DictationEngine(
        store,
        recorder=recorder,
        transcriber=transcriber,
        correction=correction,
        inserter=inserter,
        event_callback=events.append,
    )
    return engine, store, recorder, transcriber, correction, inserter, events


def _start(engine: DictationEngine, *, hands_free: bool = False) -> bool:
    return engine.start_recording(
        target_window=WindowSnapshot(101, 202, "Fault probe target"),
        hands_free=hands_free,
    )


def _wait_terminal(engine: DictationEngine) -> SessionState:
    deadline = time.monotonic() + 2.0
    while engine.state not in {SessionState.COMPLETE, SessionState.ERROR}:
        if time.monotonic() >= deadline:
            break
        time.sleep(0.005)
    worker = engine._worker
    if worker is not None and worker.is_alive():
        worker.join(timeout=1.0)
    return engine.state


def _seed_preview(store: FaultStore, engine: DictationEngine) -> None:
    super(FaultStore, store).checkpoint_recovery(
        engine._recovery_session_key,
        "Stable preview text.",
        engine.settings.mode.value,
    )


def _audio_files(directory: Path) -> list[str]:
    return sorted(
        str(path.relative_to(directory))
        for path in directory.rglob("*")
        if path.is_file() and path.suffix.casefold() in AUDIO_SUFFIXES
    )


def _result(
    name: str,
    directory: Path,
    engine: DictationEngine,
    store: FaultStore,
    recorder: FaultRecorder,
    inserter: FaultInserter,
    events: list,
    checks: dict[str, bool],
) -> dict[str, object]:
    rows = store.list_recovery_items()
    worker_alive = bool(engine._worker and engine._worker.is_alive())
    stream_alive = bool(engine._stream_thread and engine._stream_thread.is_alive())
    silence_alive = bool(engine._silence_thread and engine._silence_thread.is_alive())
    audio_files = _audio_files(directory)
    common_checks = {
        "recorder_stopped": not recorder.started,
        "worker_stopped": not worker_alive,
        "preview_stopped": not stream_alive,
        "silence_watchdog_stopped": not silence_alive,
        "timer_cleared": engine._recording_timer is None,
        "history_empty": store.list_history() == [],
        "audio_files_empty": audio_files == [],
    }
    all_checks = {**checks, **common_checks}
    warning = events[-1].warning if events else ""
    return {
        "stage": name,
        "passed": all(all_checks.values()),
        "state": engine.state.value,
        "checks": all_checks,
        "inserted_count": len(inserter.values),
        "recovery_count": len(rows),
        "recovery_status": rows[0]["status"] if rows else "",
        "recovery_text": rows[0]["final_text"] if rows else "",
        "warning": warning,
        "audio_files": audio_files,
    }


def _microphone_start_failure(directory: Path) -> dict[str, object]:
    context = _make_engine(directory)
    engine, store, recorder, _transcriber, _correction, inserter, events = context
    recorder.fail_start = True
    started = _start(engine, hands_free=True)
    result = _result(
        "microphone_start_failure",
        directory,
        engine,
        store,
        recorder,
        inserter,
        events,
        {
            "start_rejected": not started,
            "terminal_error": engine.state is SessionState.ERROR,
            "partial_capture_canceled": recorder.cancel_calls == 1,
            "hands_free_cleared": not engine.hands_free_session,
            "no_recovery_text": store.list_recovery_items() == [],
        },
    )
    engine.shutdown()
    return result


def _microphone_stop_failure(directory: Path) -> dict[str, object]:
    context = _make_engine(directory)
    engine, store, recorder, _transcriber, _correction, inserter, events = context
    started = _start(engine, hands_free=True)
    _seed_preview(store, engine)
    recorder.fail_stop = True
    stopped = engine.stop_recording()
    rows = store.list_recovery_items()
    result = _result(
        "microphone_stop_failure",
        directory,
        engine,
        store,
        recorder,
        inserter,
        events,
        {
            "start_accepted": started,
            "stop_rejected": not stopped,
            "terminal_error": engine.state is SessionState.ERROR,
            "partial_capture_canceled": recorder.cancel_calls == 1,
            "hands_free_cleared": not engine.hands_free_session,
            "checkpoint_marked_failed": bool(rows and rows[0]["status"] == "failed"),
            "stable_text_preserved": bool(
                rows and rows[0]["final_text"] == "Stable preview text."
            ),
        },
    )
    engine.shutdown()
    return result


def _microphone_cancel_failure(directory: Path) -> dict[str, object]:
    context = _make_engine(directory)
    engine, store, recorder, _transcriber, _correction, inserter, events = context
    started = _start(engine, hands_free=True)
    _seed_preview(store, engine)
    recorder.fail_stop = True
    canceled = engine.cancel_recording()
    result = _result(
        "microphone_cancel_failure",
        directory,
        engine,
        store,
        recorder,
        inserter,
        events,
        {
            "start_accepted": started,
            "cancel_reported_failure": not canceled,
            "terminal_error": engine.state is SessionState.ERROR,
            "partial_capture_canceled": recorder.cancel_calls == 1,
            "canceled_text_discarded": store.list_recovery_items() == [],
        },
    )
    engine.shutdown()
    return result


def _transcription_failure(directory: Path) -> dict[str, object]:
    context = _make_engine(directory)
    engine, store, recorder, transcriber, _correction, inserter, events = context
    transcriber.fail = True
    started = _start(engine)
    _seed_preview(store, engine)
    stopped = engine.stop_recording()
    _wait_terminal(engine)
    rows = store.list_recovery_items()
    result = _result(
        "transcription_failure",
        directory,
        engine,
        store,
        recorder,
        inserter,
        events,
        {
            "recording_completed": started and stopped,
            "terminal_error": engine.state is SessionState.ERROR,
            "checkpoint_marked_failed": bool(rows and rows[0]["status"] == "failed"),
            "stable_text_preserved": bool(
                rows and rows[0]["final_text"] == "Stable preview text."
            ),
            "nothing_inserted": inserter.values == [],
        },
    )
    engine.shutdown()
    return result


def _empty_transcription(directory: Path) -> dict[str, object]:
    context = _make_engine(directory)
    engine, store, recorder, transcriber, _correction, inserter, events = context
    transcriber.text = ""
    started = _start(engine)
    stopped = engine.stop_recording()
    _wait_terminal(engine)
    result = _result(
        "empty_transcription",
        directory,
        engine,
        store,
        recorder,
        inserter,
        events,
        {
            "recording_completed": started and stopped,
            "terminal_error": engine.state is SessionState.ERROR,
            "empty_text_not_recovered": store.list_recovery_items() == [],
            "nothing_inserted": inserter.values == [],
        },
    )
    engine.shutdown()
    return result


def _deterministic_correction_failure(directory: Path) -> dict[str, object]:
    context = _make_engine(directory)
    engine, store, recorder, _transcriber, correction, inserter, events = context
    correction.fail_deterministic = True
    started = _start(engine)
    _seed_preview(store, engine)
    stopped = engine.stop_recording()
    _wait_terminal(engine)
    rows = store.list_recovery_items()
    result = _result(
        "deterministic_correction_failure",
        directory,
        engine,
        store,
        recorder,
        inserter,
        events,
        {
            "recording_completed": started and stopped,
            "terminal_error": engine.state is SessionState.ERROR,
            "checkpoint_marked_failed": bool(rows and rows[0]["status"] == "failed"),
            "stable_text_preserved": bool(
                rows and rows[0]["final_text"] == "Stable preview text."
            ),
            "nothing_inserted": inserter.values == [],
        },
    )
    engine.shutdown()
    return result


def _refinement_failure(directory: Path, mode: DictationMode) -> dict[str, object]:
    context = _make_engine(directory, mode=mode)
    engine, store, recorder, _transcriber, correction, inserter, events = context
    correction.fail_refinement = True
    started = _start(engine)
    stopped = engine.stop_recording()
    _wait_terminal(engine)
    stage = f"{mode.value}_refinement_failure"
    result = _result(
        stage,
        directory,
        engine,
        store,
        recorder,
        inserter,
        events,
        {
            "recording_completed": started and stopped,
            "completed_with_fallback": engine.state is SessionState.COMPLETE,
            "deterministic_text_inserted_once": inserter.values
            == ["Deterministic local text. "],
            "recovery_cleared": store.list_recovery_items() == [],
            "fallback_warning_emitted": bool(
                events and "refinement failed" in events[-1].warning.casefold()
            ),
        },
    )
    engine.shutdown()
    return result


def _insertion_failure(directory: Path) -> dict[str, object]:
    context = _make_engine(directory)
    engine, store, recorder, _transcriber, _correction, inserter, events = context
    inserter.fail_insert = True
    started = _start(engine)
    stopped = engine.stop_recording()
    _wait_terminal(engine)
    rows = store.list_recovery_items()
    result = _result(
        "insertion_failure",
        directory,
        engine,
        store,
        recorder,
        inserter,
        events,
        {
            "recording_completed": started and stopped,
            "terminal_error": engine.state is SessionState.ERROR,
            "insertion_marked_uncertain": bool(
                rows
                and rows[0]["status"] == "insertion_uncertain"
                and rows[0]["final_text"] == "Deterministic local text."
            ),
            "no_automatic_retry": bool(
                events and events[-1].insertion_uncertain and "will not retry" in events[-1].message
            ),
            "nothing_inserted": inserter.values == [],
        },
    )
    engine.shutdown()
    return result


def _recovery_checkpoint_failure(directory: Path) -> dict[str, object]:
    context = _make_engine(directory)
    engine, store, recorder, _transcriber, _correction, inserter, events = context
    store.fail_checkpoint = True
    started = _start(engine)
    stopped = engine.stop_recording()
    _wait_terminal(engine)
    result = _result(
        "recovery_checkpoint_failure",
        directory,
        engine,
        store,
        recorder,
        inserter,
        events,
        {
            "recording_completed": started and stopped,
            "dictation_still_completed": engine.state is SessionState.COMPLETE,
            "text_inserted_once": inserter.values == ["Deterministic local text. "],
            "no_false_recovery": store.list_recovery_items() == [],
            "checkpoint_warning_emitted": bool(
                events and "could not be saved" in events[-1].warning.casefold()
            ),
        },
    )
    engine.shutdown()
    return result


def _recovery_mark_failure(directory: Path) -> dict[str, object]:
    context = _make_engine(directory)
    engine, store, recorder, transcriber, _correction, inserter, events = context
    transcriber.fail = True
    started = _start(engine)
    _seed_preview(store, engine)
    store.fail_mark = True
    stopped = engine.stop_recording()
    _wait_terminal(engine)
    rows = store.list_recovery_items()
    result = _result(
        "recovery_mark_failure",
        directory,
        engine,
        store,
        recorder,
        inserter,
        events,
        {
            "recording_completed": started and stopped,
            "terminal_error_still_reported": engine.state is SessionState.ERROR,
            "worker_did_not_escape": bool(
                events and events[-1].state is SessionState.ERROR
            ),
            "original_checkpoint_kept": bool(
                rows
                and rows[0]["status"] == "recording"
                and rows[0]["final_text"] == "Stable preview text."
            ),
        },
    )
    engine.shutdown()
    return result


def _recovery_cleanup_failure(directory: Path) -> dict[str, object]:
    context = _make_engine(directory)
    engine, store, recorder, _transcriber, _correction, inserter, events = context
    store.fail_delete = True
    started = _start(engine)
    stopped = engine.stop_recording()
    _wait_terminal(engine)
    rows = store.list_recovery_items()
    result = _result(
        "recovery_cleanup_failure",
        directory,
        engine,
        store,
        recorder,
        inserter,
        events,
        {
            "recording_completed": started and stopped,
            "dictation_still_completed": engine.state is SessionState.COMPLETE,
            "text_inserted_once": inserter.values == ["Deterministic local text. "],
            "cleanup_retried": store.delete_calls == 2,
            "checkpoint_left_visible": bool(
                rows
                and rows[0]["status"] == "insertion_uncertain"
                and rows[0]["final_text"] == "Deterministic local text."
            ),
            "cleanup_warning_emitted": bool(
                events and "could not be cleared" in events[-1].warning.casefold()
            ),
        },
    )
    engine.shutdown()
    return result


SCENARIOS = {
    "microphone_start_failure": _microphone_start_failure,
    "microphone_stop_failure": _microphone_stop_failure,
    "microphone_cancel_failure": _microphone_cancel_failure,
    "transcription_failure": _transcription_failure,
    "empty_transcription": _empty_transcription,
    "deterministic_correction_failure": _deterministic_correction_failure,
    "accuracy_refinement_failure": lambda directory: _refinement_failure(
        directory, DictationMode.ACCURACY
    ),
    "refine_refinement_failure": lambda directory: _refinement_failure(
        directory, DictationMode.REFINE
    ),
    "insertion_failure": _insertion_failure,
    "recovery_checkpoint_failure": _recovery_checkpoint_failure,
    "recovery_mark_failure": _recovery_mark_failure,
    "recovery_cleanup_failure": _recovery_cleanup_failure,
}


def run_fault_scenario(name: str, directory: str | Path) -> dict[str, object]:
    try:
        scenario = SCENARIOS[name]
    except KeyError as exc:
        raise ValueError(f"Unknown fault stage: {name}") from exc
    stage_directory = Path(directory)
    stage_directory.mkdir(parents=True, exist_ok=True)
    try:
        return scenario(stage_directory)
    except Exception as exc:
        return {
            "stage": name,
            "passed": False,
            "state": "probe_error",
            "checks": {},
            "error": f"{type(exc).__name__}: {exc}",
            "audio_files": _audio_files(stage_directory),
        }


def _run_fault_matrix(directory: Path) -> dict[str, object]:
    results = [
        run_fault_scenario(name, directory / name)
        for name in SCENARIOS
    ]
    return {
        "schema_version": 1,
        "local_only": True,
        "raw_audio_retained": any(result["audio_files"] for result in results),
        "stage_count": len(results),
        "passed": all(bool(result["passed"]) for result in results),
        "stages": results,
    }


def run_fault_matrix(directory: str | Path | None = None) -> dict[str, object]:
    if directory is not None:
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        return _run_fault_matrix(root)
    with tempfile.TemporaryDirectory(
        prefix="echo-link-faults-",
        ignore_cleanup_errors=True,
    ) as temporary:
        return _run_fault_matrix(Path(temporary))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run Manuscript's local deterministic reliability fault matrix."
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = run_fault_matrix()
    payload = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
