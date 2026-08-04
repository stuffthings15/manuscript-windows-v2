from __future__ import annotations

import argparse
import ctypes
import sys
import threading
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from echo_link_core.hotkeys import GlobalPushToTalk, parse_hotkey


KEYEVENTF_KEYUP = 0x0002


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe an Manuscript global hotkey")
    parser.add_argument("--hotkey", default="ctrl+win")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        print("Hotkey probe requires Windows.")
        return 1
    spec = parse_hotkey(args.hotkey, minimum_keys=1)

    pressed = threading.Event()
    released = threading.Event()
    errors: list[str] = []
    hook = GlobalPushToTalk(
        spec.canonical,
        pressed.set,
        released.set,
        errors.append,
        allow_single_key=len(spec.keys) == 1,
    )
    hook.start()
    if not hook.wait_until_ready(1.5):
        hook.stop()
        hook.join(timeout=1.0)
        print(errors[-1] if errors else hook.registration_error or "Hotkey hook did not start.")
        return 1

    user32 = ctypes.windll.user32
    try:
        for key in sorted(spec.keys):
            user32.keybd_event(key, 0, 0, 0)
        time.sleep(0.08)
        for key in sorted(spec.keys, reverse=True):
            user32.keybd_event(key, 0, KEYEVENTF_KEYUP, 0)
        press_ok = pressed.wait(1.0)
        release_ok = released.wait(1.0)
    finally:
        hook.stop()
        hook.join(timeout=1.0)

    print(f"hotkey={spec.canonical} registered={hook.registration_error == ''} press={press_ok} release={release_ok}")
    return 0 if press_ok and release_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
