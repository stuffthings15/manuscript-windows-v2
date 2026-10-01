from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path


REGISTRY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
REGISTRY_NAME = "ManuscriptWindowsV2"
LEGACY_REGISTRY_NAMES: tuple[str, ...] = ()

logger = logging.getLogger(__name__)


def startup_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}" --startup'
    runner = Path(__file__).resolve().parents[2] / "run_manuscript.py"
    return f'"{Path(sys.executable).resolve()}" "{runner}" --startup'


def configure_startup(enabled: bool) -> bool:
    """Reconcile the autostart entry. True when the registry now matches.

    BEST EFFORT, because launching matters more than autostart. This runs
    unconditionally on every start, and a managed machine can deny writes to
    the Run key by policy. It used to raise PermissionError straight into the
    startup handler in main.py, which turned "your workplace does not allow
    autostart entries" into "Manuscript could not start" - the writer could not
    use the product at all over a preference they may never have set.

    The failure is logged rather than swallowed, so a writer whose setting will
    not stick has something to point at. Returning False lets a caller say so
    on screen; nothing does yet.
    """
    if os.name != "nt":
        return False
    try:
        return _write_startup_entry(enabled)
    except OSError as exc:
        # PermissionError and the rest of the winreg family: policy, a locked
        # hive, or a profile that is not writable.
        logger.warning(
            "Could not reconcile the Windows startup entry (%s: %s). "
            "Manuscript still runs; only 'start with Windows' is affected.",
            type(exc).__name__,
            exc,
        )
        return False


def _write_startup_entry(enabled: bool) -> bool:
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REGISTRY_PATH) as key:
        if enabled:
            winreg.SetValueEx(key, REGISTRY_NAME, 0, winreg.REG_SZ, startup_command())
        for name in (REGISTRY_NAME, *LEGACY_REGISTRY_NAMES):
            if enabled and name == REGISTRY_NAME:
                continue
            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass
    return True


def open_log_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        subprocess.Popen(["explorer.exe", str(path)], close_fds=True)
