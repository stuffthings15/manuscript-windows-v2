from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

import pyperclip

from selected_text_live_probe import (
    VK_A,
    VK_CONTROL,
    VK_V,
    combo,
    notepad_window,
    titled_window,
    window_for_pid,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe automatic Writing HUD selection detection.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        print("Windows only")
        return 2

    echo = titled_window("Manuscript")
    if not echo:
        print("Manuscript window not found")
        return 3
    ctypes.windll.user32.ShowWindow(echo, 5)
    ctypes.windll.user32.PostMessageW(echo, 0x0010, 0, 0)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and ctypes.windll.user32.IsWindowVisible(echo):
        time.sleep(0.05)

    old_hud = titled_window("Manuscript writing HUD", starts_with=True)
    if old_hud:
        ctypes.windll.user32.PostMessageW(old_hud, 0x0010, 0, 0)

    note = subprocess.Popen(["notepad.exe"])
    handle = 0
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not handle:
            handle = window_for_pid(note.pid) or notepad_window()
            time.sleep(0.05)
        if not handle:
            print("Notepad window not found")
            return 4

        ctypes.windll.user32.SetForegroundWindow(handle)
        pyperclip.copy("this are automatic writing HUD text")
        combo(VK_CONTROL, VK_V)
        sentinel = "ECHOLINK_AUTO_HUD_CLIPBOARD_SENTINEL"
        pyperclip.copy(sentinel)
        combo(VK_CONTROL, VK_A)
        target_before = int(ctypes.windll.user32.GetForegroundWindow() or 0)

        deadline = time.monotonic() + 8.0
        hud = 0
        while time.monotonic() < deadline:
            hud = titled_window("Manuscript writing HUD", starts_with=True)
            if hud and ctypes.windll.user32.IsWindowVisible(hud):
                break
            time.sleep(0.05)

        visible = bool(hud and ctypes.windll.user32.IsWindowVisible(hud))
        target_after = int(ctypes.windll.user32.GetForegroundWindow() or 0)
        focus_preserved = target_before == handle and target_after == handle
        clipboard_safe = pyperclip.paste() == sentinel
        print(f"automatic_writing_hud_visible={visible}")
        print(f"target_focus_preserved={focus_preserved}")
        print(f"clipboard_untouched={clipboard_safe}")
        passed = visible and focus_preserved and clipboard_safe
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "probe": "automatic_writing_hud",
                        "passed": passed,
                        "checks": {
                            "automatic_writing_hud_visible": visible,
                            "target_focus_preserved": focus_preserved,
                            "windows_clipboard_untouched": clipboard_safe,
                            "manual_hotkey_sent": False,
                        },
                        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "limitations": [
                            "The probe uses one live Windows Notepad selection.",
                            "Editors that do not expose a Windows accessibility text selection use the manual hotkey fallback.",
                        ],
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
        if hud:
            ctypes.windll.user32.PostMessageW(hud, 0x0010, 0, 0)
        return 0 if passed else 5
    finally:
        if handle:
            ctypes.windll.user32.PostMessageW(handle, 0x0010, 0, 0)
        note.terminate()
        try:
            note.wait(timeout=3)
        except subprocess.TimeoutExpired:
            note.kill()


if __name__ == "__main__":
    raise SystemExit(main())
