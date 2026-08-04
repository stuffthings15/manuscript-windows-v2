from __future__ import annotations

from pathlib import Path

import pytest

from tools import installer_lifecycle_probe as probe


def test_installer_command_is_silent_isolated_and_icon_free(tmp_path: Path) -> None:
    installer = tmp_path / "ManuscriptSetup.exe"
    install_dir = tmp_path / "install"
    log = tmp_path / "install.log"

    command = probe._installer_command(installer, install_dir, log)

    assert command[0] == str(installer)
    assert "/VERYSILENT" in command
    assert "/SUPPRESSMSGBOXES" in command
    assert "/NORESTART" in command
    assert "/NOICONS" in command
    assert f"/DIR={install_dir}" in command
    assert f"/LOG={log}" in command


def test_uninstaller_command_is_silent(tmp_path: Path) -> None:
    uninstaller = tmp_path / "unins000.exe"
    log = tmp_path / "uninstall.log"

    command = probe._uninstaller_command(uninstaller, log)

    assert command == [
        str(uninstaller),
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        f"/LOG={log}",
    ]


def test_diagnostics_summary_is_allowlisted() -> None:
    summary = probe._summarize_diagnostics(
        {
            "app": "Manuscript",
            "runtime": "local-only",
            "code_commit": "abc123",
            "schema_version": 8,
            "speech_model_ready": True,
            "hotkey_registered": True,
            "private_paste": {"hotkey_registered": True, "secret": "do not copy"},
            "database_security": {"encrypted": True, "cipher": "SQLCipher"},
            "private_text": "never include this",
        }
    )

    assert summary["database_encrypted"] is True
    assert summary["private_paste_hotkey_registered"] is True
    assert "private_text" not in summary
    assert "secret" not in summary


def test_safe_cleanup_refuses_non_probe_directory(tmp_path: Path) -> None:
    target = tmp_path / "ordinary-folder"
    target.mkdir()

    with pytest.raises(RuntimeError, match="Refused"):
        probe._safe_remove_probe_root(target, tmp_path)

    assert target.is_dir()
    target.rmdir()


def test_safe_cleanup_removes_only_generated_probe_directory(tmp_path: Path) -> None:
    target = tmp_path / f"{probe.PROBE_PREFIX}unit"
    target.mkdir()
    (target / "sentinel.txt").write_text("temporary", encoding="utf-8")

    assert probe._safe_remove_probe_root(target, tmp_path) is True
    assert not target.exists()


def test_required_checks_cover_full_lifecycle_and_privacy() -> None:
    assert len(probe.REQUIRED_CHECKS) == 21
    assert "installed_runtime_bundle" in probe.REQUIRED_CHECKS
    assert "repair_restored_resource" in probe.REQUIRED_CHECKS
    assert "silent_reinstall" in probe.REQUIRED_CHECKS
    assert "final_uninstall" in probe.REQUIRED_CHECKS
    assert "private_data_preserved" in probe.REQUIRED_CHECKS
    assert "temporary_probe_data_removed" in probe.REQUIRED_CHECKS


def test_uninstall_entry_matches_stable_app_id_and_current_display_name() -> None:
    assert probe._is_echo_link_uninstall_entry(
        "{71099095-BCB5-4E9C-A623-D0C474335542}_is1",
        "Manuscript version 0.1.0",
    )
    assert probe._is_echo_link_uninstall_entry("unrelated", "Manuscript")
    assert not probe._is_echo_link_uninstall_entry("unrelated", "Other app")


def test_uninstaller_uses_verified_registry_path_inside_install_folder(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    uninstaller = install_dir / "unins001.exe"
    uninstaller.write_bytes(b"probe")

    assert probe._uninstaller_from_entry(
        {"uninstall": f'"{uninstaller}"'}, install_dir
    ) == uninstaller.resolve()

    with pytest.raises(RuntimeError, match="outside"):
        probe._uninstaller_from_entry(
            {"uninstall": f'"{tmp_path / "other.exe"}"'}, install_dir
        )


def test_remaining_file_inventory_contains_names_only(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    nested = install_dir / "runtime"
    nested.mkdir(parents=True)
    (nested / "engine.dll").write_bytes(b"private payload must not be read")

    assert probe._remaining_relative_files(install_dir) == ["runtime/engine.dll"]


def test_empty_install_directory_counts_as_no_program_files(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    install_dir.mkdir()

    assert probe._wait_for_no_files(install_dir, timeout=0.1)
