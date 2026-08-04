from __future__ import annotations

import argparse
import ctypes
import time
from ctypes import wintypes

from echo_link_core.storage import EchoLinkStore


KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_ALT = 0x12
VK_SPACE = 0x20
VK_ESCAPE = 0x1B
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


def visible_windows(process_id: int) -> set[int]:
    handles: set[int] = set()
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def visit(handle, _extra):
        owner = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        if int(owner.value) == process_id and ctypes.windll.user32.IsWindowVisible(handle):
            handles.add(int(handle))
        return True

    ctypes.windll.user32.EnumWindows(visit, 0)
    return handles


def tap(*keys: int) -> None:
    for key in keys:
        ctypes.windll.user32.keybd_event(key, 0, 0, 0)
    time.sleep(0.08)
    for key in reversed(keys):
        ctypes.windll.user32.keybd_event(key, 0, KEYEVENTF_KEYUP, 0)


def rect(handle: int) -> tuple[int, int, int, int]:
    value = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(handle, ctypes.byref(value))
    return value.left, value.top, value.right, value.bottom


def drag(handle: int, dx: int = 140, dy: int = 90) -> None:
    left, top, right, bottom = rect(handle)
    start_x, start_y = (left + right) // 2, (top + bottom) // 2
    ctypes.windll.user32.SetCursorPos(start_x, start_y)
    ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    for step in range(1, 8):
        ctypes.windll.user32.SetCursorPos(
            start_x + dx * step // 7,
            start_y + dy * step // 7,
        )
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_MOVE, 0, 0, 0, 0)
        time.sleep(0.04)
    ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--verify-saved", action="store_true")
    args = parser.parse_args()

    baseline = visible_windows(args.pid)
    tap(VK_CONTROL, VK_ALT, VK_SPACE)
    time.sleep(0.8)
    added = visible_windows(args.pid) - baseline
    if not added:
        print("Manuscript Bar did not appear")
        return 2
    handle = next(iter(added))
    settings = EchoLinkStore().get_settings()
    if args.verify_saved:
        current = rect(handle)
        ok = current[0] == settings.echo_bar_x and current[1] == settings.echo_bar_y
        print(f"saved_position=({settings.echo_bar_x},{settings.echo_bar_y}) live_position=({current[0]},{current[1]})")
    else:
        before = rect(handle)
        drag(handle)
        time.sleep(0.5)
        after = rect(handle)
        settings = EchoLinkStore().get_settings()
        moved = after[:2] != before[:2]
        saved = after[0] == settings.echo_bar_x and after[1] == settings.echo_bar_y
        ok = moved and saved
        print(f"before={before[:2]} after={after[:2]} moved={moved} saved={saved}")
    tap(VK_ESCAPE)
    time.sleep(0.5)
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
