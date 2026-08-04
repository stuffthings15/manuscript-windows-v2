from __future__ import annotations

import asyncio
import argparse
import ctypes
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from echo_link_core.media_control import MediaPauseController
from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager


ROOT = Path(__file__).resolve().parents[1]
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_ALT = 0x12
VK_SPACE = 0x20
VK_ESCAPE = 0x1B


async def sessions():
    manager = await GlobalSystemMediaTransportControlsSessionManager.request_async()
    return list(manager.get_sessions())


def status_name(session) -> str:
    status = session.get_playback_info().playback_status
    return str(getattr(status, "name", status)).casefold()


def wait_for_status(expected: str, timeout: float = 8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = asyncio.run(sessions())
        matches = [item for item in current if "chrome" in item.source_app_user_model_id.casefold()]
        if any(status_name(item) == expected for item in matches):
            return True
        time.sleep(0.1)
    return False


def session_snapshot():
    return [
        (item.source_app_user_model_id, status_name(item))
        for item in asyncio.run(sessions())
    ]


def tap(*keys: int) -> None:
    for key in keys:
        ctypes.windll.user32.keybd_event(key, 0, 0, 0)
    time.sleep(0.08)
    for key in reversed(keys):
        ctypes.windll.user32.keybd_event(key, 0, KEYEVENTF_KEYUP, 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--through-app", action="store_true")
    args = parser.parse_args()
    if not CHROME.is_file():
        print("Chrome not found")
        return 2
    profile = Path(tempfile.mkdtemp(prefix="echo-link-media-probe-"))
    process = subprocess.Popen(
        [
            str(CHROME),
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--disable-extensions",
            "--autoplay-policy=no-user-gesture-required",
            "--enable-features=HardwareMediaKeyHandling,MediaSessionService",
            "--disable-background-media-suspend",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--window-position=-32000,-32000",
            (ROOT / "tools" / "media_probe.html").as_uri(),
        ],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    controller = None if args.through_app else MediaPauseController()
    try:
        playing = wait_for_status("playing")
        if not playing:
            print(f"sessions={session_snapshot()}")
        if args.through_app:
            tap(VK_CONTROL, VK_ALT, VK_SPACE)
        else:
            controller.pause()
        paused = wait_for_status("paused") if playing else False
        if args.through_app:
            tap(VK_ESCAPE)
        else:
            controller.resume()
        resumed = wait_for_status("playing") if paused else False
        print(f"playing_detected={playing}")
        print(f"paused_by_echo_link={paused}")
        print(f"resumed_by_echo_link={resumed}")
        return 0 if playing and paused and resumed else 3
    finally:
        if controller is not None:
            controller.shutdown()
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
