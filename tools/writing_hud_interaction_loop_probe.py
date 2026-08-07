from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from writing_hud_apply_probe import (
    _accept_button,
    _descendants,
    _dismiss_existing_hud,
    _document_text,
    _editable_control,
    _wait_for_target,
    _window_with_prefix,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = "This are teh bad sentence."
EXPECTED = "This is a bad sentence."


def _visible_hud(timeout: float):
    return _window_with_prefix("Manuscript writing HUD", timeout)


def _badge_button(hud):
    for control in _descendants(hud):
        try:
            if control.ControlTypeName == "ButtonControl" and control.IsEnabled:
                return control
        except Exception:
            continue
    return None


def _click(control) -> bool:
    try:
        control.Click(simulateMove=False, waitTime=0.05)
        return True
    except Exception:
        return False


def _logical_size(control) -> tuple[int, int]:
    rectangle = control.BoundingRectangle
    dpi = int(ctypes.windll.user32.GetDpiForWindow(control.NativeWindowHandle) or 96)
    scale = max(1.0, dpi / 96.0)
    return round(rectangle.width() / scale), round(rectangle.height() / scale)


def _run_cycle(index: int, *, ai_timeout: float) -> dict[str, object]:
    _dismiss_existing_hud()
    target_root = tempfile.TemporaryDirectory(
        prefix=f"manuscript-hud-loop-{index:02d}-",
        ignore_cleanup_errors=True,
    )
    target_status = Path(target_root.name) / "target.json"
    target_title = f"Manuscript HUD loop target {index} {time.time_ns()}"
    process = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "tools" / "writing_hud_apply_probe.py"),
            "--target-mode",
            "--target-title",
            target_title,
            "--target-status",
            str(target_status),
        ],
        cwd=ROOT,
    )
    handle = 0
    result: dict[str, object] = {
        "cycle": index,
        "empty_hidden": False,
        "first_character_visible": False,
        "correction_box_opened": False,
        "ai_ready": False,
        "ai_wait_seconds": 0.0,
        "replacement_applied": False,
        "elapsed_seconds": 0.0,
    }
    started = time.monotonic()
    try:
        handle = _wait_for_target(target_status, process, 6.0)
        if not handle:
            result["error"] = "isolated editor did not start"
            return result
        ctypes.windll.user32.SetForegroundWindow(handle)
        editor = _editable_control(handle)
        if editor is None:
            result["error"] = "editable control not found"
            return result
        editor.Click()
        editor.SetFocus()
        # A badge from the previously focused app can arrive while this
        # isolated editor is still starting. Clear it only after the empty
        # editor owns focus, then give the monitor one complete poll window.
        time.sleep(0.35)
        _dismiss_existing_hud()
        ctypes.windll.user32.SetForegroundWindow(handle)
        editor.Click()
        editor.SetFocus()

        result["empty_hidden"] = _visible_hud(0.8) is None
        if not result["empty_hidden"]:
            result["error"] = "icon appeared before typing"
            return result

        editor.SendKeys(SOURCE[0], interval=0.02, waitTime=0.05)
        hud = _visible_hud(3.0)
        result["first_character_visible"] = bool(
            hud is not None and max(_logical_size(hud)) <= 36
        )
        if not result["first_character_visible"]:
            result["error"] = "icon did not appear after first character"
            return result

        editor.SendKeys(SOURCE[1:], interval=0.01, waitTime=0.05)
        time.sleep(0.65)
        hud = _visible_hud(2.0)
        if hud is None:
            result["error"] = "icon disappeared before click"
            return result
        badge = _badge_button(hud)
        if badge is None or not _click(badge):
            result["error"] = "icon click failed"
            return result

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if min(_logical_size(hud)) > 36:
                result["correction_box_opened"] = True
                break
            time.sleep(0.05)
        if not result["correction_box_opened"]:
            result["error"] = "correction box did not open"
            return result

        ai_started = time.monotonic()
        deadline = ai_started + ai_timeout
        accept = None
        while time.monotonic() < deadline:
            accept = _accept_button(hud)
            if accept is not None and accept.IsEnabled:
                result["ai_ready"] = True
                break
            time.sleep(0.05)
        result["ai_wait_seconds"] = round(time.monotonic() - ai_started, 3)
        if not result["ai_ready"]:
            result["error"] = "AI correction did not become ready"
            return result
        if not _click(accept):
            result["error"] = "Replace Text click failed"
            return result

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                current_text = _document_text(handle).strip()
            except Exception:
                current_text = ""
            result["actual_text"] = current_text
            if current_text == EXPECTED:
                result["replacement_applied"] = True
                break
            time.sleep(0.05)
        if not result["replacement_applied"]:
            result["error"] = "corrected text was not inserted"
        return result
    finally:
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        _dismiss_existing_hud()
        if handle:
            ctypes.windll.user32.PostMessageW(handle, 0x0010, 0, 0)
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
        target_root.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Repeat the real automatic Writing HUD interaction until stable."
    )
    parser.add_argument("--cycles", type=int, default=10)
    parser.add_argument("--ai-timeout", type=float, default=10.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        print("Windows only")
        return 2
    if not 1 <= args.cycles <= 50:
        print("cycles must be between 1 and 50")
        return 2
    if not 1.0 <= args.ai_timeout <= 90.0:
        print("ai-timeout must be between 1 and 90 seconds")
        return 2

    results = []
    for index in range(1, args.cycles + 1):
        result = _run_cycle(index, ai_timeout=args.ai_timeout)
        results.append(result)
        cycle_passed = all(
            bool(result[name])
            for name in (
                "empty_hidden",
                "first_character_visible",
                "correction_box_opened",
                "ai_ready",
                "replacement_applied",
            )
        )
        print(
            f"cycle={index} passed={cycle_passed} "
            f"elapsed={result['elapsed_seconds']} error={result.get('error', '')}",
            flush=True,
        )
        if result.get("error"):
            break

    passed = len(results) == args.cycles and all(
        all(
            bool(result[name])
            for name in (
                "empty_hidden",
                "first_character_visible",
                "correction_box_opened",
                "ai_ready",
                "replacement_applied",
            )
        )
        for result in results
    )
    payload = {
        "schema_version": 1,
        "probe": "writing_hud_interaction_loop",
        "passed": passed,
        "requested_cycles": args.cycles,
        "completed_cycles": len(results),
        "source": SOURCE,
        "expected": EXPECTED,
        "results": results,
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    print(rendered)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if passed else 5


if __name__ == "__main__":
    raise SystemExit(main())
