from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REGISTRY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
REGISTRY_NAME = "Manuscript"
LEGACY_REGISTRY_NAMES = ("EchoLink",)


def startup_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}" --startup'
    runner = Path(__file__).resolve().parents[2] / "run_echo_link.py"
    return f'"{Path(sys.executable).resolve()}" "{runner}" --startup'


def configure_startup(enabled: bool) -> None:
    if os.name != "nt":
        return
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


def open_log_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        subprocess.Popen(["explorer.exe", str(path)], close_fds=True)
