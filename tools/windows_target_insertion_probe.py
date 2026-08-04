from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
import sys
import time
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

import pyperclip


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from echo_link_core.data_protection import validate_local_export_destination


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _window(title: str) -> tuple[int, int]:
    user32 = ctypes.windll.user32
    matches: list[tuple[int, int]] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(handle, _lparam):
        length = int(user32.GetWindowTextLengthW(handle))
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(handle, buffer, length + 1)
        if buffer.value == title:
            process_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
            matches.append((int(handle), int(process_id.value)))
        return True

    user32.EnumWindows(callback_type(visit), 0)
    if len(matches) != 1:
        raise RuntimeError(f"Expected one window titled {title!r}; found {len(matches)}.")
    return matches[0]


def _write_json(path: Path, payload: dict) -> None:
    path = validate_local_export_destination(
        path,
        allowed_suffixes={".json"},
        overwrite=True,
        create_parent=True,
    )
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    from echo_link_core.insertion import WindowSnapshot, WindowsTextInserter, active_window
    from echo_link_core.models import InsertionMode

    parser = argparse.ArgumentParser(description="Probe insertion into one exact Windows title")
    parser.add_argument("--title", required=True)
    parser.add_argument("--mode", choices=[item.value for item in InsertionMode], required=True)
    parser.add_argument("--text-b64", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--delay", type=float, default=3.0)
    args = parser.parse_args(argv)

    report = {
        "schema_version": 1,
        "probe": "windows_target_insertion",
        "started_at": _utc_now(),
        "title": args.title,
        "mode": args.mode,
        "passed": False,
    }
    original_clipboard = None
    try:
        text = base64.b64decode(args.text_b64, validate=True).decode("utf-8")
        report["requested_text"] = text
        time.sleep(max(0.0, args.delay))
        handle, process_id = _window(args.title)
        report["window_handle"] = handle
        report["process_id"] = process_id
        report["active_before"] = active_window().handle == handle
        try:
            original_clipboard = pyperclip.paste()
        except Exception:
            original_clipboard = None
        sentinel = f"ECHOLINK_TARGET_PROBE_{os.getpid()}"
        pyperclip.copy(sentinel)
        record = WindowsTextInserter(InsertionMode(args.mode)).insert_into(
            WindowSnapshot(handle, process_id, args.title),
            text,
        )
        time.sleep(0.5)
        report["record_created"] = record is not None
        report["active_after"] = active_window().handle == handle
        report["clipboard_restored"] = pyperclip.paste() == sentinel
        report["passed"] = bool(report["record_created"] and report["clipboard_restored"])
    except Exception as exc:
        report["error"] = str(exc)
    finally:
        if original_clipboard is not None:
            try:
                pyperclip.copy(original_clipboard)
            except Exception as exc:
                report["clipboard_final_restore_error"] = str(exc)
                report["passed"] = False
        report["finished_at"] = _utc_now()
        _write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
