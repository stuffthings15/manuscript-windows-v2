from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import platform
import re
import secrets
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pyperclip


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from echo_link_core.insertion import WindowSnapshot, WindowsTextInserter, active_window
from echo_link_core.models import InsertionMode


SCHEMA_VERSION = 1
AUTOMATED_CASE_IDS = (
    "external_standard_textbox",
    "external_chat_textbox",
    "external_terminal",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _source_provenance(root: Path = ROOT) -> dict[str, Any]:
    commit = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    commit_id = commit.stdout.strip().casefold()
    return {
        "source_commit": commit_id if re.fullmatch(r"[0-9a-f]{40}", commit_id) else "",
        "source_tree_clean_at_probe_start": bool(
            status.returncode == 0 and not status.stdout.strip()
        ),
    }


def _manual_gates(process_elevated: bool) -> list[dict[str, Any]]:
    return [
        {
            "id": "elevated_window_uipi",
            "status": "manual_required",
            "automated": False,
            "automatic_elevation_attempted": False,
            "current_probe_process_elevated": bool(process_elevated),
            "acceptance": (
                "From a normal Manuscript process, target an explicitly approved elevated "
                "test editor. Confirm insertion is blocked and the error names the Windows "
                "UIPI boundary. Do not bypass UIPI."
            ),
        },
        {
            "id": "physical_microphone_and_bluetooth",
            "status": "manual_required",
            "automated": False,
            "acceptance": (
                "Use a physical microphone and Bluetooth headset in the release build. "
                "Confirm the final text reaches each approved target once."
            ),
        },
        {
            "id": "third_party_application_matrix",
            "status": "manual_required",
            "automated": False,
            "acceptance": (
                "Repeat with real Word, browser chat, and editor windows. This probe uses "
                "isolated controls and a real local command terminal only."
            ),
        },
    ]


def collect_evidence(
    runner: "WindowsAcceptanceRunner",
    *,
    provenance: dict[str, Any] | None = None,
    started_at: str | None = None,
) -> dict[str, Any]:
    started = started_at or _utc_now()
    methods: tuple[tuple[str, Callable[[], dict[str, Any]]], ...] = (
        (AUTOMATED_CASE_IDS[0], runner.run_standard_textbox),
        (AUTOMATED_CASE_IDS[1], runner.run_chat_textbox),
        (AUTOMATED_CASE_IDS[2], runner.run_terminal),
    )
    cases: list[dict[str, Any]] = []
    for case_id, method in methods:
        try:
            result = dict(method())
            result["id"] = case_id
            result["status"] = "passed" if result.pop("passed", False) else "failed"
        except Exception as exc:
            result = {
                "id": case_id,
                "status": "failed",
                "error_type_sha256": _sha256(type(exc).__name__),
            }
        cases.append(result)

    passed_count = sum(item["status"] == "passed" for item in cases)
    gates = _manual_gates(runner.process_elevated)
    report = {
        "schema_version": SCHEMA_VERSION,
        "probe": "windows_external_insertion_acceptance",
        "status": "passed" if passed_count == len(cases) else "failed",
        "local_only": True,
        "network_used": False,
        "user_data_deleted_or_overwritten": False,
        "started_at": started,
        "finished_at": _utc_now(),
        "environment": {
            "platform": platform.system(),
            "platform_release": platform.release(),
            "process_elevated": runner.process_elevated,
        },
        "provenance": provenance or _source_provenance(),
        "automated_cases": cases,
        "manual_gates": gates,
        "summary": {
            "automated_total": len(cases),
            "automated_passed": passed_count,
            "automated_failed": len(cases) - passed_count,
            "manual_gate_count": len(gates),
            "manual_gates_closed": False,
        },
    }
    return report


class WindowsAcceptanceRunner:
    def __init__(self, *, timeout_seconds: float = 8.0):
        if sys.platform != "win32":
            raise RuntimeError("This acceptance runner requires Windows.")
        self.timeout_seconds = max(2.0, min(float(timeout_seconds), 30.0))
        self.process_elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())

    def run_standard_textbox(self) -> dict[str, Any]:
        nonce = secrets.token_hex(6).upper()
        return self._run_text_target(
            "standard",
            f"Manuscript standard textbox {nonce}",
        )

    def run_chat_textbox(self) -> dict[str, Any]:
        nonce = secrets.token_hex(6).upper()
        return self._run_text_target(
            "chat",
            f"Manuscript chat line one {nonce}\nLine two Ω",
        )

    def _run_text_target(self, kind: str, expected: str) -> dict[str, Any]:
        nonce = secrets.token_hex(8).upper()
        title = f"Manuscript Acceptance {kind} {nonce}"
        result: dict[str, Any] = {}
        process: subprocess.Popen | None = None
        snapshot: WindowSnapshot | None = None
        clipboard_ok, original_clipboard = _clipboard_backup()
        if not clipboard_ok:
            raise RuntimeError("Clipboard text could not be backed up safely.")
        with tempfile.TemporaryDirectory(prefix="echo-link-external-target-") as directory:
            state_path = Path(directory) / "target-state.json"
            try:
                environment = os.environ.copy()
                environment.pop("QT_QPA_PLATFORM", None)
                process = subprocess.Popen(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "--target-child",
                        kind,
                        "--target-title",
                        title,
                        "--target-state",
                        str(state_path),
                    ],
                    cwd=ROOT,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                state = _wait_for_state(
                    state_path,
                    lambda value: bool(value.get("ready")),
                    self.timeout_seconds,
                )
                snapshot = _wait_for_window(title, self.timeout_seconds)
                sentinel = f"ECHOLINK_CLIPBOARD_{nonce}"
                pyperclip.copy(sentinel)
                record = WindowsTextInserter(InsertionMode.AUTO).insert_into(snapshot, expected)
                state = _wait_for_state(
                    state_path,
                    lambda value: value.get("text") == expected,
                    self.timeout_seconds,
                    last=state,
                )
                clipboard_restored = _wait_for_clipboard(sentinel, 2.0)
                actual = str(state.get("text", ""))
                submitted_count = int(state.get("submitted_count", 0))
                target_pid = int(state.get("pid", 0))
                external_process_verified = _external_process_verified(
                    snapshot.process_id,
                    target_pid,
                )
                exact_text_match = actual == expected
                chat_submission_safe = kind != "chat" or submitted_count == 0
                result.update(
                    {
                        "target_kind": (
                            "external_standard_textbox"
                            if kind == "standard"
                            else "external_chat_like_textbox"
                        ),
                        "insertion_mode": InsertionMode.AUTO.value,
                        "record_created": record is not None,
                        "external_process_verified": external_process_verified,
                        "window_pid_matches_target_self_report": bool(
                            snapshot.process_id == target_pid
                        ),
                        "launcher_pid_matches_target": bool(process.pid == target_pid),
                        "exact_text_match": exact_text_match,
                        "expected_utf8_sha256": _sha256(expected),
                        "actual_utf8_sha256": _sha256(actual),
                        "expected_character_count": len(expected),
                        "actual_character_count": len(actual),
                        "clipboard_backup_available": clipboard_ok,
                        "clipboard_restored": clipboard_restored,
                        "chat_submission_count": submitted_count if kind == "chat" else None,
                        "chat_not_submitted": chat_submission_safe if kind == "chat" else None,
                        "passed": bool(
                            record is not None
                            and external_process_verified
                            and exact_text_match
                            and clipboard_ok
                            and clipboard_restored
                            and chat_submission_safe
                        ),
                    }
                )
                return result
            finally:
                original_clipboard_restored = _restore_clipboard(original_clipboard)
                closed_normally = _close_owned_process(process, snapshot)
                if result:
                    result["original_clipboard_text_restored"] = (
                        original_clipboard_restored
                    )
                    result["target_closed_normally"] = closed_normally
                    result["passed"] = bool(
                        result.get("passed")
                        and original_clipboard_restored
                        and closed_normally
                    )

    def run_terminal(self) -> dict[str, Any]:
        nonce = secrets.token_hex(8).upper()
        title = f"Manuscript Acceptance Terminal {nonce}"
        marker = f"ECHOLINK_TERMINAL_{nonce}"
        command = _terminal_command(marker)
        result: dict[str, Any] = {}
        process: subprocess.Popen | None = None
        snapshot: WindowSnapshot | None = None
        clipboard_ok, original_clipboard = _clipboard_backup()
        if not clipboard_ok:
            raise RuntimeError("Clipboard text could not be backed up safely.")
        with tempfile.TemporaryDirectory(prefix="echo-link-terminal-target-") as directory:
            output_path = Path(directory) / "terminal-result.txt"
            environment = os.environ.copy()
            environment["ECHOLINK_ACCEPTANCE_OUTPUT"] = str(output_path)
            try:
                process = subprocess.Popen(
                    ["cmd.exe", "/D", "/Q", "/K", f"title {title}"],
                    cwd=directory,
                    env=environment,
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                )
                snapshot = _wait_for_window(title, self.timeout_seconds)
                sentinel = f"ECHOLINK_CLIPBOARD_{nonce}"
                pyperclip.copy(sentinel)
                record = WindowsTextInserter(InsertionMode.AUTO).insert_into(snapshot, command)
                enter_sent = bool(record is not None and _press_enter(snapshot))
                output_ready = _wait_for_file(output_path, self.timeout_seconds)
                actual = output_path.read_text(encoding="utf-8").strip() if output_ready else ""
                clipboard_restored = _wait_for_clipboard(sentinel, 2.0)
                exact_marker_match = actual == marker
                result.update(
                    {
                        "target_kind": "external_windows_command_terminal",
                        "insertion_mode": InsertionMode.AUTO.value,
                        "record_created": record is not None,
                        "external_process_verified": bool(
                            snapshot.process_id != os.getpid()
                        ),
                        "safe_echo_command_verified": command == _terminal_command(marker),
                        "enter_sent": enter_sent,
                        "marker_file_created": output_ready,
                        "exact_marker_match": exact_marker_match,
                        "expected_utf8_sha256": _sha256(marker),
                        "actual_utf8_sha256": _sha256(actual),
                        "clipboard_backup_available": clipboard_ok,
                        "clipboard_restored": clipboard_restored,
                        "passed": bool(
                            record is not None
                            and snapshot.process_id != os.getpid()
                            and enter_sent
                            and output_ready
                            and exact_marker_match
                            and clipboard_ok
                            and clipboard_restored
                        ),
                    }
                )
                return result
            finally:
                original_clipboard_restored = _restore_clipboard(original_clipboard)
                closed_normally = _close_owned_process(process, snapshot)
                if result:
                    result["original_clipboard_text_restored"] = (
                        original_clipboard_restored
                    )
                    result["target_closed_normally"] = closed_normally
                    result["passed"] = bool(
                        result.get("passed")
                        and original_clipboard_restored
                        and closed_normally
                    )


def _terminal_command(marker: str) -> str:
    if not re.fullmatch(r"[A-Z0-9_]{8,80}", marker):
        raise ValueError("Terminal marker must contain safe uppercase characters only.")
    return f'echo {marker}>"%ECHOLINK_ACCEPTANCE_OUTPUT%"'


def _external_process_verified(window_pid: int, target_pid: int) -> bool:
    return bool(target_pid > 0 and window_pid == target_pid and target_pid != os.getpid())


def _clipboard_backup() -> tuple[bool, str]:
    try:
        return True, str(pyperclip.paste())
    except Exception:
        return False, ""


def _restore_clipboard(value: str) -> bool:
    try:
        pyperclip.copy(value)
    except Exception:
        return False
    return _wait_for_clipboard(value, 1.0)


def _wait_for_clipboard(expected: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if pyperclip.paste() == expected:
                return True
        except Exception:
            pass
        time.sleep(0.03)
    return False


def _wait_for_file(path: Path, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file() and path.stat().st_size > 0:
            return True
        time.sleep(0.04)
    return False


def _wait_for_state(
    path: Path,
    predicate: Callable[[dict[str, Any]], bool],
    timeout: float,
    *,
    last: dict[str, Any] | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    current = dict(last or {})
    while time.monotonic() < deadline:
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            time.sleep(0.03)
            continue
        if predicate(current):
            return current
        time.sleep(0.03)
    return current


def _wait_for_window(title: str, timeout: float) -> WindowSnapshot:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = _find_window(title)
        if snapshot is not None:
            return snapshot
        time.sleep(0.04)
    raise RuntimeError("External target window did not appear.")


def _find_window(title: str) -> WindowSnapshot | None:
    if sys.platform != "win32":
        return None
    user32 = ctypes.windll.user32
    matches: list[WindowSnapshot] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(handle, _lparam):
        if not user32.IsWindowVisible(handle):
            return True
        length = int(user32.GetWindowTextLengthW(handle))
        if length != len(title):
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(handle, buffer, length + 1)
        if buffer.value != title:
            return True
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
        matches.append(WindowSnapshot(int(handle), int(process_id.value), title))
        return True

    callback = callback_type(visit)
    user32.EnumWindows(callback, 0)
    return matches[0] if len(matches) == 1 else None


def _press_enter(snapshot: WindowSnapshot) -> bool:
    current = active_window()
    if current.handle != snapshot.handle or current.process_id != snapshot.process_id:
        return False
    user32 = ctypes.windll.user32
    keyeventf_keyup = 0x0002
    vk_return = 0x0D
    user32.keybd_event(vk_return, 0, 0, 0)
    user32.keybd_event(vk_return, 0, keyeventf_keyup, 0)
    return True


def _close_owned_process(
    process: subprocess.Popen | None,
    snapshot: WindowSnapshot | None,
) -> bool:
    if process is None:
        return True
    window_closed = True
    if snapshot is not None and sys.platform == "win32":
        user32 = ctypes.windll.user32
        if user32.IsWindow(snapshot.handle):
            user32.PostMessageW(snapshot.handle, 0x0010, 0, 0)
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and user32.IsWindow(snapshot.handle):
                time.sleep(0.03)
            window_closed = not bool(user32.IsWindow(snapshot.handle))
    if process.poll() is not None:
        return window_closed
    if window_closed:
        try:
            process.wait(timeout=2.0)
            return True
        except subprocess.TimeoutExpired:
            pass
    process.terminate()
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2.0)
    return False


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    destination = path.expanduser().resolve(strict=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=True, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _run_target_child(kind: str, title: str, state_path: Path) -> int:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import (
        QApplication,
        QLabel,
        QLineEdit,
        QPlainTextEdit,
        QVBoxLayout,
        QWidget,
    )

    class ChatEdit(QPlainTextEdit):
        def __init__(self):
            super().__init__()
            self.submitted_count = 0

        def keyPressEvent(self, event: QKeyEvent) -> None:
            enter = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            shifted = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if enter and not shifted:
                self.submitted_count += 1
                self.clear()
                return
            super().keyPressEvent(event)

    app = QApplication.instance() or QApplication([])
    window = QWidget()
    window.setWindowTitle(title)
    window.resize(620, 170)
    layout = QVBoxLayout(window)
    if kind == "standard":
        edit = QLineEdit()
        layout.addWidget(QLabel("Standard external textbox"))
    else:
        edit = ChatEdit()
        layout.addWidget(QLabel("Chat-like textbox. Enter submits; Shift+Enter adds a line."))
    layout.addWidget(edit)
    window.show()
    window.raise_()
    window.activateWindow()
    edit.setFocus()
    previous = ""

    def publish() -> None:
        nonlocal previous
        text = edit.text() if isinstance(edit, QLineEdit) else edit.toPlainText()
        payload = {
            "ready": True,
            "pid": os.getpid(),
            "text": text,
            "submitted_count": int(getattr(edit, "submitted_count", 0)),
        }
        serialized = json.dumps(payload, ensure_ascii=True, sort_keys=True)
        if serialized != previous:
            _write_json_atomic(state_path, payload)
            previous = serialized

    timer = QTimer(window)
    timer.timeout.connect(publish)
    timer.start(40)
    publish()
    return int(app.exec())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run safe local acceptance for external Windows text targets."
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--target-child", choices=("standard", "chat"))
    parser.add_argument("--target-title")
    parser.add_argument("--target-state", type=Path)
    args = parser.parse_args(argv)

    if args.target_child:
        if not args.target_title or args.target_state is None:
            parser.error("Target child mode requires --target-title and --target-state.")
        return _run_target_child(args.target_child, args.target_title, args.target_state)

    if args.output is None:
        parser.error("--output is required.")
    provenance = _source_provenance()
    started_at = _utc_now()
    try:
        runner = WindowsAcceptanceRunner(timeout_seconds=args.timeout)
        report = collect_evidence(
            runner,
            provenance=provenance,
            started_at=started_at,
        )
    except Exception as exc:
        report = {
            "schema_version": SCHEMA_VERSION,
            "probe": "windows_external_insertion_acceptance",
            "status": "failed",
            "local_only": True,
            "network_used": False,
            "started_at": started_at,
            "finished_at": _utc_now(),
            "provenance": provenance,
            "error_type_sha256": _sha256(type(exc).__name__),
            "manual_gates": _manual_gates(False),
        }
    _write_json_atomic(args.output, report)
    print(
        json.dumps(
            {
                "probe": report["probe"],
                "status": report["status"],
                "summary": report.get("summary", {}),
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
