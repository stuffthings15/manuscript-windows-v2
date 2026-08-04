from __future__ import annotations

import csv
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .data_protection import validate_local_directory


SYSTEM_SID = "S-1-5-18"
ADMINISTRATORS_SID = "S-1-5-32-544"
COMMON_BROAD_SIDS = ("S-1-1-0", "S-1-5-11", "S-1-5-32-545")
PRIVATE_FILENAMES = (
    "echo-link.db",
    "echo-link.db-wal",
    "echo-link.db-shm",
    ".echo-link.db.encrypted-migration",
    "echo-link.key.dpapi",
    "echo-link.key.pending.dpapi",
    "echo-link.log",
    "echo-link.log.1",
    "echo-link.log.2",
    "echo-link.log.3",
    "echo-link.lock",
    "manuscript-subscription.dpapi",
)
_SID_PATTERN = re.compile(r"^S-\d+(?:-\d+)+$", re.IGNORECASE)
_CREATE_NO_WINDOW = 0x08000000


class DataFolderProtectionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DataFolderProtectionResult:
    root: Path
    current_user_sid: str
    existing_private_files: int
    hardened_private_files: int
    verified_targets: int
    encryption_enabled: bool = False
    secure_erase_enabled: bool = False


Runner = Callable[..., subprocess.CompletedProcess[str]]


def harden_windows_data_folder(
    root: str | Path,
    *,
    _runner: Runner = subprocess.run,
    _sid_provider: Callable[[], str] | None = None,
) -> DataFolderProtectionResult:
    """Reduce ACLs on Manuscript's data root and existing private files."""
    if sys.platform != "win32" and _sid_provider is None:
        raise DataFolderProtectionError("Windows data-folder hardening requires Windows.")
    checked_root = validate_local_directory(
        root,
        purpose="Manuscript data folder",
        create=False,
    )
    sid = (_sid_provider or current_user_sid)()
    if not _SID_PATTERN.fullmatch(sid):
        raise DataFolderProtectionError("The current Windows user SID is invalid.")

    targets = [checked_root]
    targets.extend(
        path
        for name in PRIVATE_FILENAMES
        if (path := checked_root / name).is_file() and not path.is_symlink()
    )
    for target in targets:
        is_directory = target == checked_root
        inheritance = "(OI)(CI)F" if is_directory else "F"
        command = [
            "icacls.exe",
            str(target),
            "/inheritance:r",
            "/remove:g",
            *(f"*{broad_sid}" for broad_sid in COMMON_BROAD_SIDS),
            "/grant:r",
            f"*{sid}:{inheritance}",
            f"*{SYSTEM_SID}:{inheritance}",
            f"*{ADMINISTRATORS_SID}:{inheritance}",
            "/Q",
        ]
        _run_icacls(command, _runner)

    verified = 0
    for target in targets:
        _run_icacls(["icacls.exe", str(target), "/verify", "/Q"], _runner)
        verified += 1
    return DataFolderProtectionResult(
        root=checked_root,
        current_user_sid=sid,
        existing_private_files=max(0, len(targets) - 1),
        hardened_private_files=max(0, len(targets) - 1),
        verified_targets=verified,
    )


def current_user_sid(*, _runner: Runner = subprocess.run) -> str:
    completed = _runner(
        ["whoami.exe", "/user", "/fo", "csv", "/nh"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=False,
        creationflags=_CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if completed.returncode != 0:
        raise DataFolderProtectionError("Windows could not identify the current user SID.")
    for row in csv.reader(completed.stdout.splitlines()):
        for value in row:
            candidate = value.strip()
            if _SID_PATTERN.fullmatch(candidate):
                return candidate.upper()
    raise DataFolderProtectionError("Windows did not return a valid current user SID.")


def _run_icacls(command: list[str], runner: Runner) -> None:
    completed = runner(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
        creationflags=_CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if completed.returncode != 0:
        raise DataFolderProtectionError("Windows could not harden the Manuscript data folder.")
