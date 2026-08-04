from __future__ import annotations

import os
from pathlib import Path


def is_network_path(value: str | os.PathLike[str]) -> bool:
    """Return True for UNC paths and Windows drives mapped to remote storage."""

    raw = os.fspath(value)
    if raw.startswith(("\\\\", "//")):
        return True
    if os.name != "nt":
        return False
    drive = Path(raw).expanduser().drive
    if not drive:
        return False
    return _windows_drive_type(f"{drive}\\") == 4


def _windows_drive_type(root: str) -> int:
    try:
        import ctypes

        return int(ctypes.windll.kernel32.GetDriveTypeW(root))
    except (AttributeError, OSError, TypeError, ValueError):
        return 0


__all__ = ["is_network_path"]
