from __future__ import annotations

import subprocess

import pytest

from echo_link_core.data_folder_acl import (
    ADMINISTRATORS_SID,
    COMMON_BROAD_SIDS,
    SYSTEM_SID,
    DataFolderProtectionError,
    current_user_sid,
    harden_windows_data_folder,
)


def test_hardening_targets_root_and_only_fixed_private_files(tmp_path) -> None:
    for name in (
        "echo-link.db",
        "echo-link.db-wal",
        "echo-link.key.dpapi",
        "echo-link.key.pending.dpapi",
        ".echo-link.db.encrypted-migration",
        "echo-link.log",
        "unrelated.txt",
    ):
        (tmp_path / name).write_text("local", encoding="utf-8")
    commands = []

    def runner(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    result = harden_windows_data_folder(
        tmp_path,
        _runner=runner,
        _sid_provider=lambda: "S-1-5-21-1-2-3-1001",
    )

    harden = [command for command in commands if "/grant:r" in command]
    verify = [command for command in commands if "/verify" in command]
    assert len(harden) == 7
    assert len(verify) == 7
    assert result.existing_private_files == 6
    assert result.hardened_private_files == 6
    assert result.verified_targets == 7
    assert all("unrelated.txt" not in " ".join(command) for command in commands)
    root_command = harden[0]
    assert f"*{result.current_user_sid}:(OI)(CI)F" in root_command
    assert f"*{SYSTEM_SID}:(OI)(CI)F" in root_command
    assert f"*{ADMINISTRATORS_SID}:(OI)(CI)F" in root_command
    assert all(f"*{sid}" in root_command for sid in COMMON_BROAD_SIDS)
    assert result.encryption_enabled is False
    assert result.secure_erase_enabled is False


def test_hardening_rejects_invalid_sid_before_acl_change(tmp_path) -> None:
    calls = []
    with pytest.raises(DataFolderProtectionError, match="SID is invalid"):
        harden_windows_data_folder(
            tmp_path,
            _runner=lambda *args, **kwargs: calls.append((args, kwargs)),
            _sid_provider=lambda: "not-a-sid",
        )
    assert calls == []


def test_current_user_sid_parses_csv_without_returning_account_name() -> None:
    def runner(command, **_kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            '"WORKSTATION\\private-user","S-1-5-21-1-2-3-1001"\n',
            "",
        )

    assert current_user_sid(_runner=runner) == "S-1-5-21-1-2-3-1001"


def test_acl_command_failure_is_fail_closed(tmp_path) -> None:
    def runner(command, **_kwargs):
        return subprocess.CompletedProcess(command, 5, "", "denied")

    with pytest.raises(DataFolderProtectionError, match="could not harden"):
        harden_windows_data_folder(
            tmp_path,
            _runner=runner,
            _sid_provider=lambda: "S-1-5-21-1-2-3-1001",
        )
