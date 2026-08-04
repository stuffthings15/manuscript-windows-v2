from __future__ import annotations

import argparse
import ctypes
import time
from ctypes import wintypes


KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_ALT = 0x12
VK_SPACE = 0x20
VK_ESCAPE = 0x1B


def visible_windows_for_process(process_id: int) -> list[int]:
    user32 = ctypes.windll.user32
    handles: list[int] = []
    callback_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
        wintypes.BOOL,
        wintypes.HWND,
        wintypes.LPARAM,
    )

    @callback_type
    def callback(window, _extra):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, ctypes.byref(owner))
        if int(owner.value) == int(process_id) and user32.IsWindowVisible(window):
            handles.append(int(window))
        return True

    user32.EnumWindows(callback, 0)
    return handles


def tap(keys: tuple[int, ...]) -> None:
    user32 = ctypes.windll.user32
    for key in keys:
        user32.keybd_event(key, 0, 0, 0)
    time.sleep(0.08)
    for key in reversed(keys):
        user32.keybd_event(key, 0, KEYEVENTF_KEYUP, 0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe live Manuscript hands-free toggle and Esc cancel")
    parser.add_argument("--pid", type=int, required=True)
    args = parser.parse_args(argv)

    baseline = visible_windows_for_process(args.pid)
    tap((VK_CONTROL, VK_ALT, VK_SPACE))
    time.sleep(0.8)
    listening = visible_windows_for_process(args.pid)
    tap((VK_ESCAPE,))
    time.sleep(1.8)
    canceled = visible_windows_for_process(args.pid)

    hud_appeared = len(listening) > len(baseline)
    hud_closed = len(canceled) < len(listening)
    print(
        f"pid={args.pid} baseline_windows={len(baseline)} "
        f"listening_windows={len(listening)} canceled_windows={len(canceled)} "
        f"hud_appeared={hud_appeared} hud_closed={hud_closed}"
    )
    return 0 if hud_appeared and hud_closed else 1


if __name__ == "__main__":
    raise SystemExit(main())
