from __future__ import annotations

import argparse
import json
import os
import subprocess
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


CRASH_EXIT = 17
STABLE_TEXT = "Stable listening text."
FINAL_TEXT = "Deterministic crash text."
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


class CrashStore(EchoLinkStore):
    def __init__(self, path: str | Path) -> None:
        self.exit_after_final_checkpoint = False
        self.exit_before_cleanup = False
        super().__init__(path)

    def checkpoint_recovery(self, *args, **kwargs) -> int:
        item_id = super().checkpoint_recovery(*args, **kwargs)
        text = str(args[1] if len(args) > 1 else kwargs.get("final_text", "")).strip()
        status = str(kwargs.get("status", "recording"))
        if self.exit_after_final_checkpoint and text == FINAL_TEXT and status == "recording":
            os._exit(CRASH_EXIT)
        return item_id

    def delete_recovery_session(self, *args, **kwargs) -> None:
        if self.exit_before_cleanup:
            os._exit(CRASH_EXIT)
        super().delete_recovery_session(*args, **kwargs)


class ChildRecorder:
    def start(self, *_args, **_kwargs) -> None:
        pass

    def stop(self) -> np.ndarray:
        return np.ones(8000, dtype=np.int16)

    def cancel(self) -> None:
        pass


class ChildTranscriber:
    def __init__(self, stage: str) -> None:
        self.stage = stage

    def transcribe(self, *_args, **_kwargs) -> str:
        if self.stage == "transcribing":
            os._exit(CRASH_EXIT)
        return "local crash probe text"

    def update_settings(self, _settings) -> None:
        pass

    def release_model(self) -> None:
        pass


class ChildCorrection:
    def __init__(self, stage: str) -> None:
        self.stage = stage

    def deterministic(self, _raw_text: str) -> CorrectionResult:
        if self.stage == "deterministic_correction":
            os._exit(CRASH_EXIT)
        return CorrectionResult(FINAL_TEXT + " ", "deterministic")

    def correct(self, _raw_text: str, _mode: DictationMode) -> CorrectionResult:
        if self.stage in {"accuracy_refinement", "post_insert_refinement"}:
            os._exit(CRASH_EXIT)
        return CorrectionResult(FINAL_TEXT + " ", "deterministic")

    def update_settings(self, _settings) -> None:
        pass


class ChildInserter:
    def __init__(self, stage: str, marker: Path) -> None:
        self.stage = stage
        self.marker = marker

    def insert_into(self, _window, _text: str):
        with self.marker.open("a", encoding="utf-8") as stream:
            stream.write("possible external insertion\n")
            stream.flush()
            os.fsync(stream.fileno())
        if self.stage == "insertion_uncertain":
            os._exit(CRASH_EXIT)
        return object()

    def replace_if_safe(self, _record, _text: str):
        return object()

    def undo_last(self) -> bool:
        return False


CRASH_STAGES = (
    "recording",
    "transcribing",
    "deterministic_correction",
    "after_deterministic_checkpoint",
    "accuracy_refinement",
    "insertion_uncertain",
    "cleanup_uncertain",
    "post_insert_refinement",
    "normal_completion",
)


EXPECTED = {
    "recording": (CRASH_EXIT, "recording", STABLE_TEXT, 0),
    "transcribing": (CRASH_EXIT, "recording", STABLE_TEXT, 0),
    "deterministic_correction": (CRASH_EXIT, "recording", STABLE_TEXT, 0),
    "after_deterministic_checkpoint": (CRASH_EXIT, "recording", FINAL_TEXT, 0),
    "accuracy_refinement": (CRASH_EXIT, "recording", FINAL_TEXT, 0),
    "insertion_uncertain": (CRASH_EXIT, "insertion_uncertain", FINAL_TEXT, 1),
    "cleanup_uncertain": (CRASH_EXIT, "insertion_uncertain", FINAL_TEXT, 1),
    "post_insert_refinement": (CRASH_EXIT, "", "", 1),
    "normal_completion": (0, "", "", 1),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Crash Manuscript at engine boundaries and verify recovery after relaunch."
    )
    parser.add_argument("--child-database")
    parser.add_argument("--stage", choices=CRASH_STAGES)
    parser.add_argument("--marker")
    parser.add_argument("--output", type=Path)
    return parser


def _crashing_child(database: Path, stage: str, marker: Path) -> int:
    store = CrashStore(database)
    settings = store.get_settings()
    settings.keep_history = False
    settings.keep_recovery_text = True
    settings.streaming_enabled = False
    settings.silence_auto_stop_enabled = False
    settings.max_recording_seconds = 0
    settings.mode = (
        DictationMode.ACCURACY
        if stage == "accuracy_refinement"
        else DictationMode.REFINE
        if stage == "post_insert_refinement"
        else DictationMode.FAST
    )
    store.save_settings(settings)
    store.exit_after_final_checkpoint = stage == "after_deterministic_checkpoint"
    store.exit_before_cleanup = stage == "cleanup_uncertain"
    engine = DictationEngine(
        store,
        recorder=ChildRecorder(),
        transcriber=ChildTranscriber(stage),
        correction=ChildCorrection(stage),
        inserter=ChildInserter(stage, marker),
    )
    if not engine.start_recording(
        target_window=WindowSnapshot(101, 202, "Crash probe target")
    ):
        return 3
    if stage in {"recording", "transcribing", "deterministic_correction"}:
        store.checkpoint_recovery(
            engine._recovery_session_key,
            STABLE_TEXT,
            settings.mode.value,
        )
    if stage == "recording":
        os._exit(CRASH_EXIT)
    if not engine.stop_recording():
        return 4
    deadline = time.monotonic() + 5.0
    while engine.state not in {SessionState.COMPLETE, SessionState.ERROR}:
        if time.monotonic() >= deadline:
            return 5
        time.sleep(0.01)
    worker = engine._worker
    if worker is not None and worker.is_alive():
        worker.join(timeout=1.0)
    return 0 if engine.state is SessionState.COMPLETE else 6


def _marker_count(marker: Path) -> int:
    if not marker.exists():
        return 0
    return len([line for line in marker.read_text(encoding="utf-8").splitlines() if line])


def _audio_files(directory: Path) -> list[str]:
    return sorted(
        str(path.relative_to(directory))
        for path in directory.rglob("*")
        if path.is_file() and path.suffix.casefold() in AUDIO_SUFFIXES
    )


def _run_stage(stage: str, directory: Path) -> dict[str, object]:
    directory.mkdir(parents=True, exist_ok=True)
    database = directory / "echo-link.db"
    marker = directory / "external-insertion.marker"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(SRC)
    child = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--child-database",
            str(database),
            "--stage",
            stage,
            "--marker",
            str(marker),
        ],
        env=environment,
        check=False,
        timeout=15,
    )

    marker_before_relaunch = _marker_count(marker)
    first_rows = EchoLinkStore(database).list_recovery_items()
    relaunched_store = EchoLinkStore(database)
    rows = relaunched_store.list_recovery_items()
    marker_after_relaunch = _marker_count(marker)
    audio_files = _audio_files(directory)
    expected_exit, expected_status, expected_text, expected_insertions = EXPECTED[stage]
    row = rows[0] if rows else None
    actual_status = str(row["status"]) if row else ""
    actual_text = str(row["final_text"]) if row else ""
    uncertainty_explicit = (
        actual_status != "insertion_uncertain"
        or bool(
            row
            and "Check the target" in str(row["error_message"])
            and "will not retry" in str(row["error_message"])
        )
    )
    checks = {
        "expected_exit": child.returncode == expected_exit,
        "single_recovery_row_or_none": len(rows) == (1 if expected_status else 0),
        "expected_recovery_status": actual_status == expected_status,
        "expected_recovery_text": actual_text == expected_text,
        "uncertainty_is_explicit": uncertainty_explicit,
        "expected_external_insertion_count": marker_before_relaunch == expected_insertions,
        "relaunch_did_not_insert_again": marker_after_relaunch == marker_before_relaunch,
        "relaunch_read_is_stable": first_rows == rows,
        "history_empty": relaunched_store.list_history() == [],
        "audio_files_empty": audio_files == [],
    }
    return {
        "stage": stage,
        "passed": all(checks.values()),
        "child_exit": child.returncode,
        "checks": checks,
        "recovery_count": len(rows),
        "recovery_status": actual_status,
        "recovery_text": actual_text,
        "external_insertions_before_relaunch": marker_before_relaunch,
        "external_insertions_after_relaunch": marker_after_relaunch,
        "automatic_retry_observed": marker_after_relaunch != marker_before_relaunch,
        "audio_files": audio_files,
    }


def _run_crash_matrix(directory: Path) -> dict[str, object]:
    stages = [_run_stage(stage, directory / stage) for stage in CRASH_STAGES]
    return {
        "schema_version": 2,
        "local_only": True,
        "raw_audio_retained": any(stage["audio_files"] for stage in stages),
        "stage_count": len(stages),
        "passed": all(bool(stage["passed"]) for stage in stages),
        "stages": stages,
    }


def run_crash_matrix(directory: str | Path | None = None) -> dict[str, object]:
    if directory is not None:
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        return _run_crash_matrix(root)
    with tempfile.TemporaryDirectory(
        prefix="echo-link-crashes-",
        ignore_cleanup_errors=True,
    ) as temporary:
        return _run_crash_matrix(Path(temporary))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.child_database:
        if not args.stage or not args.marker:
            return 2
        return _crashing_child(Path(args.child_database), args.stage, Path(args.marker))

    report = run_crash_matrix()
    payload = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
