from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from echo_link_app.windows_identity import APP_MUTEX_NAME
from echo_link_core.data_protection import (
    validate_local_data_file,
    validate_local_directory,
    validate_local_export_destination,
)


SCHEMA_VERSION = 1
PROBE_PREFIX = "echo-link-installer-lifecycle-"
APP_ID = "65BD5BC9-6E5E-4E2D-9BB1-B49EA46CB73E"
DISPLAY_NAME = "Manuscript Windows Version 2"
REQUIRED_CHECKS = (
    "no_existing_install",
    "app_not_running",
    "silent_install",
    "installed_registry_entry",
    "installed_executable",
    "installed_model",
    "installed_runtime_bundle",
    "installed_smoke",
    "installed_diagnostics",
    "installed_workflow",
    "repair_restored_resource",
    "silent_uninstall",
    "program_files_removed",
    "uninstall_registry_removed",
    "private_data_preserved",
    "silent_reinstall",
    "reinstalled_smoke",
    "reinstalled_model",
    "final_uninstall",
    "final_private_data_preserved",
    "temporary_probe_data_removed",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _installer_command(installer: Path, install_dir: Path, log_path: Path) -> list[str]:
    return [
        str(installer),
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/SP-",
        "/NOICONS",
        f"/DIR={install_dir}",
        f"/LOG={log_path}",
    ]


def _uninstaller_command(uninstaller: Path, log_path: Path) -> list[str]:
    return [
        str(uninstaller),
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        f"/LOG={log_path}",
    ]


def _run(
    command: Iterable[str],
    *,
    env: dict[str, str],
    timeout: float,
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    started = time.perf_counter()
    completed = subprocess.run(
        list(command),
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(1.0, float(timeout)),
        check=False,
    )
    summary = {
        "exit_code": int(completed.returncode),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
    return completed, summary


def _wait_for_absence(path: Path, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + max(0.1, timeout)
    while time.monotonic() < deadline:
        if not path.exists():
            return True
        time.sleep(0.1)
    return not path.exists()


def _remaining_relative_files(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    return sorted(
        str(path.relative_to(root)).replace("\\", "/")
        for path in root.rglob("*")
        if path.is_file()
    )


def _wait_for_no_files(root: Path, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + max(0.1, timeout)
    while time.monotonic() < deadline:
        if not _remaining_relative_files(root):
            return True
        time.sleep(0.1)
    return not _remaining_relative_files(root)


def _mutex_exists(name: str = APP_MUTEX_NAME) -> bool:
    if sys.platform != "win32":
        return False
    synchronize = 0x00100000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_mutex = kernel32.OpenMutexW
    open_mutex.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    open_mutex.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    handle = open_mutex(synchronize, False, name)
    if not handle:
        return False
    close_handle(handle)
    return True


def _uninstall_entries() -> list[dict[str, str]]:
    if sys.platform != "win32":
        return []
    import winreg

    paths = (r"Software\Microsoft\Windows\CurrentVersion\Uninstall",)
    roots = (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
    views = (0, winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY)
    found: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for root in roots:
        for base in paths:
            for view in views:
                try:
                    parent = winreg.OpenKey(root, base, 0, winreg.KEY_READ | view)
                except OSError:
                    continue
                try:
                    count = winreg.QueryInfoKey(parent)[0]
                    for index in range(count):
                        try:
                            child_name = winreg.EnumKey(parent, index)
                            child = winreg.OpenKey(parent, child_name)
                            try:
                                display = str(winreg.QueryValueEx(child, "DisplayName")[0])
                            except OSError:
                                display = ""
                            if not _is_echo_link_uninstall_entry(child_name, display):
                                winreg.CloseKey(child)
                                continue
                            try:
                                location = str(winreg.QueryValueEx(child, "InstallLocation")[0])
                            except OSError:
                                location = ""
                            try:
                                uninstall = str(winreg.QueryValueEx(child, "UninstallString")[0])
                            except OSError:
                                uninstall = ""
                            winreg.CloseKey(child)
                            identity = (child_name, location.casefold())
                            if identity not in seen:
                                seen.add(identity)
                                found.append(
                                    {
                                        "key": child_name,
                                        "location": location,
                                        "uninstall": uninstall,
                                    }
                                )
                        except OSError:
                            continue
                finally:
                    winreg.CloseKey(parent)
    return found


def _is_echo_link_uninstall_entry(child_name: str, display_name: str) -> bool:
    expected_key = f"{{{APP_ID}}}_is1".casefold()
    return bool(
        str(child_name).casefold() == expected_key
        or str(display_name).strip() == DISPLAY_NAME
    )


def _matching_uninstall_entry(install_dir: Path) -> dict[str, str] | None:
    expected = os.path.normcase(str(install_dir.resolve(strict=False))).rstrip("\\/")
    for entry in _uninstall_entries():
        location = os.path.normcase(entry["location"]).rstrip("\\/")
        if location == expected:
            return entry
    return None


def _uninstaller_from_entry(entry: dict[str, str], install_dir: Path) -> Path:
    raw = str(entry.get("uninstall", "")).strip()
    if raw.startswith('"') and '"' in raw[1:]:
        candidate_text = raw[1:].split('"', 1)[0]
    else:
        marker = raw.casefold().find(".exe")
        candidate_text = raw[: marker + 4] if marker >= 0 else raw
    candidate = Path(candidate_text).resolve(strict=False)
    expected_parent = install_dir.resolve(strict=False)
    if (
        candidate.parent != expected_parent
        or not candidate.name.casefold().startswith("unins")
        or candidate.suffix.casefold() != ".exe"
    ):
        raise RuntimeError("Refused an uninstaller outside the isolated install folder.")
    return candidate


def _wait_for_uninstall_entry_absence(install_dir: Path, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + max(0.1, timeout)
    while time.monotonic() < deadline:
        if _matching_uninstall_entry(install_dir) is None:
            return True
        time.sleep(0.1)
    return _matching_uninstall_entry(install_dir) is None


def _installed_paths(install_dir: Path) -> tuple[Path, Path, Path]:
    executable = install_dir / "ManuscriptV2.exe"
    model = install_dir / "_internal" / "models" / "base.en" / "model.bin"
    repair_asset = install_dir / "_internal" / "resources" / "manuscript.png"
    return executable, model, repair_asset


def _runtime_dependency_inventory(install_dir: Path) -> dict[str, bool]:
    internal = install_dir / "_internal"
    return {
        "speech_model": (internal / "models" / "base.en" / "model.bin").is_file(),
        "speech_engine": (internal / "ctranslate2" / "ctranslate2.dll").is_file(),
        "desktop_ui": (internal / "PySide6" / "QtWidgets.pyd").is_file(),
        "windows_ui_plugin": (
            internal / "PySide6" / "plugins" / "platforms" / "qwindows.dll"
        ).is_file(),
        "audio_runtime": (
            internal
            / "_sounddevice_data"
            / "portaudio-binaries"
            / "libportaudio64bit.dll"
        ).is_file(),
        "encrypted_database_runtime": (internal / "sqlite3.dll").is_file(),
        "built_in_activation_cues": True,
    }


def _summarize_diagnostics(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Installed diagnostics did not return an object.")
    security = payload.get("database_security")
    if not isinstance(security, dict):
        security = {}
    private_paste = payload.get("private_paste")
    if not isinstance(private_paste, dict):
        private_paste = {}
    return {
        "app": str(payload.get("app", "")),
        "runtime": str(payload.get("runtime", "")),
        "code_commit": str(payload.get("code_commit", "")),
        "schema_version": int(payload.get("schema_version", 0)),
        "speech_model_ready": bool(payload.get("speech_model_ready")),
        "hotkey_registered": bool(payload.get("hotkey_registered")),
        "private_paste_hotkey_registered": bool(private_paste.get("hotkey_registered")),
        "database_encrypted": bool(security.get("encrypted")),
        "database_cipher": str(security.get("cipher", "")),
    }


def _signature_status(path: Path) -> str:
    if sys.platform != "win32":
        return "not checked"
    escaped = str(path).replace("'", "''")
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"(Get-AuthenticodeSignature -LiteralPath '{escaped}').Status",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    return completed.stdout.strip() or "unknown"


def _safe_remove_probe_root(root: Path, parent: Path) -> bool:
    resolved_root = root.resolve(strict=False)
    resolved_parent = parent.resolve(strict=True)
    if resolved_root.parent != resolved_parent or not resolved_root.name.startswith(PROBE_PREFIX):
        raise RuntimeError("Refused to remove a path outside the generated lifecycle probe root.")
    if resolved_root.exists():
        shutil.rmtree(resolved_root)
    return not resolved_root.exists()


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    rendered = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run_probe(args: argparse.Namespace) -> dict[str, Any]:
    if sys.platform != "win32":
        raise RuntimeError("The installer lifecycle probe requires Windows.")
    installer = validate_local_data_file(
        args.installer,
        purpose="Manuscript installer",
        allowed_suffixes={".exe"},
    )
    output = validate_local_export_destination(
        args.output,
        allowed_suffixes={".json"},
        overwrite=True,
        create_parent=True,
    )
    work_parent = validate_local_directory(
        args.work_root or Path(tempfile.gettempdir()),
        purpose="Installer lifecycle work folder",
        create=True,
    )

    checks = {name: False for name in REQUIRED_CHECKS}
    existing = _uninstall_entries()
    checks["no_existing_install"] = not existing
    checks["app_not_running"] = not _mutex_exists()
    if existing:
        raise RuntimeError("An Manuscript installation already exists. Remove it before this probe.")
    if not checks["app_not_running"]:
        raise RuntimeError("Manuscript is running. Close it before this probe.")

    root = Path(tempfile.mkdtemp(prefix=PROBE_PREFIX, dir=work_parent))
    install_dir = root / "install"
    data_dir = root / "data"
    report_dir = root / "reports"
    install_dir.mkdir()
    data_dir.mkdir()
    report_dir.mkdir()
    sentinel = data_dir / "lifecycle-sentinel.bin"
    sentinel.write_bytes(b"Manuscript installer lifecycle sentinel v1\n")
    sentinel_hash = _sha256(sentinel)

    env = os.environ.copy()
    env.update(
        {
            "ECHOLINK_DATA_DIR": str(data_dir),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "NO_PROXY": "*",
        }
    )
    runs: dict[str, Any] = {}
    diagnostics_summary: dict[str, Any] = {}
    workflow_summary: dict[str, Any] = {}
    repair_summary: dict[str, Any] = {}
    dependency_inventory: dict[str, bool] = {}
    first_uninstall_summary: dict[str, Any] = {}
    second_uninstall_summary: dict[str, Any] = {}
    first_uninstall_remaining: list[str] = []
    final_uninstall_remaining: list[str] = []
    cleanup_complete = False
    active_uninstaller: Path | None = None
    started_at = _utc_now()

    try:
        completed, runs["install"] = _run(
            _installer_command(installer, install_dir, report_dir / "install.log"),
            env=env,
            timeout=args.timeout,
        )
        checks["silent_install"] = completed.returncode == 0
        install_entry = _matching_uninstall_entry(install_dir)
        checks["installed_registry_entry"] = install_entry is not None
        if install_entry is not None:
            possible_uninstaller = _uninstaller_from_entry(install_entry, install_dir)
            if possible_uninstaller.is_file():
                active_uninstaller = possible_uninstaller
        executable, model, cue = _installed_paths(install_dir)
        checks["installed_executable"] = executable.is_file()
        checks["installed_model"] = model.is_file() and model.stat().st_size > 1_000_000
        dependency_inventory = _runtime_dependency_inventory(install_dir)
        checks["installed_runtime_bundle"] = all(dependency_inventory.values())
        if not all(
            checks[name]
            for name in (
                "silent_install",
                "installed_registry_entry",
                "installed_executable",
                "installed_model",
                "installed_runtime_bundle",
            )
        ):
            failed = [
                name
                for name in (
                    "silent_install",
                    "installed_registry_entry",
                    "installed_executable",
                    "installed_model",
                    "installed_runtime_bundle",
                )
                if not checks[name]
            ]
            raise RuntimeError(
                "Silent install did not create the required package state: "
                + ", ".join(failed)
            )

        completed, runs["smoke"] = _run(
            [str(executable), "--smoke-test"], env=env, timeout=60
        )
        checks["installed_smoke"] = completed.returncode == 0

        completed, runs["diagnostics"] = _run(
            [str(executable), "--diagnostics"], env=env, timeout=60
        )
        if completed.returncode == 0:
            diagnostics_summary = _summarize_diagnostics(json.loads(completed.stdout))
        checks["installed_diagnostics"] = bool(
            completed.returncode == 0
            and diagnostics_summary.get("runtime") == "local-only"
            and diagnostics_summary.get("speech_model_ready")
            and diagnostics_summary.get("database_encrypted")
        )

        workflow_path = report_dir / "workflow.json"
        completed, runs["workflow"] = _run(
            [
                str(executable),
                "--workflow-probe",
                "--probe-output",
                str(workflow_path),
                "--probe-timeout",
                "90",
            ],
            env=env,
            timeout=max(args.timeout, 120),
        )
        workflow_payload = (
            json.loads(workflow_path.read_text(encoding="utf-8"))
            if workflow_path.is_file()
            else {}
        )
        workflow_checks = workflow_payload.get("checks", {})
        workflow_summary = {
            "passed": bool(workflow_payload.get("passed")),
            "checks": len(workflow_checks) if isinstance(workflow_checks, dict) else 0,
            "all_checks_passed": bool(
                isinstance(workflow_checks, dict)
                and workflow_checks
                and all(value is True for value in workflow_checks.values())
            ),
        }
        checks["installed_workflow"] = bool(
            completed.returncode == 0
            and workflow_summary["passed"]
            and workflow_summary["checks"] == 20
            and workflow_summary["all_checks_passed"]
        )

        if not cue.is_file():
            raise RuntimeError("The installed app icon was not found for the repair test.")
        cue_hash = _sha256(cue)
        cue.write_bytes(b"damaged by isolated Manuscript repair probe\n")
        damaged_hash = _sha256(cue)
        completed, runs["repair"] = _run(
            _installer_command(installer, install_dir, report_dir / "repair.log"),
            env=env,
            timeout=args.timeout,
        )
        restored_hash = _sha256(cue) if cue.is_file() else ""
        checks["repair_restored_resource"] = bool(
            completed.returncode == 0
            and damaged_hash != cue_hash
            and restored_hash == cue_hash
        )
        repair_summary = {
            "resource": "manuscript.png",
            "original_sha256": cue_hash,
            "damaged_sha256": damaged_hash,
            "restored_sha256": restored_hash,
        }

        repair_entry = _matching_uninstall_entry(install_dir)
        if repair_entry is None:
            raise RuntimeError("Repair removed the Manuscript uninstall entry.")
        active_uninstaller = _uninstaller_from_entry(repair_entry, install_dir)
        if not active_uninstaller.is_file():
            raise RuntimeError("Repair did not leave a usable Manuscript uninstaller.")

        completed, first_uninstall_summary = _run(
            _uninstaller_command(active_uninstaller, report_dir / "uninstall-1.log"),
            env=env,
            timeout=args.timeout,
        )
        checks["silent_uninstall"] = completed.returncode == 0
        checks["program_files_removed"] = _wait_for_no_files(install_dir, timeout=30)
        first_uninstall_remaining = _remaining_relative_files(install_dir)
        checks["uninstall_registry_removed"] = _wait_for_uninstall_entry_absence(install_dir)
        checks["private_data_preserved"] = bool(
            sentinel.is_file() and _sha256(sentinel) == sentinel_hash
        )
        active_uninstaller = None

        completed, runs["reinstall"] = _run(
            _installer_command(installer, install_dir, report_dir / "reinstall.log"),
            env=env,
            timeout=args.timeout,
        )
        checks["silent_reinstall"] = completed.returncode == 0
        executable, model, _cue = _installed_paths(install_dir)
        reinstall_entry = _matching_uninstall_entry(install_dir)
        if reinstall_entry is None:
            raise RuntimeError("Reinstall did not create the Manuscript uninstall entry.")
        active_uninstaller = _uninstaller_from_entry(reinstall_entry, install_dir)
        completed, runs["reinstalled_smoke"] = _run(
            [str(executable), "--smoke-test"], env=env, timeout=60
        )
        checks["reinstalled_smoke"] = completed.returncode == 0
        checks["reinstalled_model"] = model.is_file() and model.stat().st_size > 1_000_000

        completed, second_uninstall_summary = _run(
            _uninstaller_command(active_uninstaller, report_dir / "uninstall-2.log"),
            env=env,
            timeout=args.timeout,
        )
        checks["final_uninstall"] = bool(
            completed.returncode == 0
            and _wait_for_no_files(install_dir, timeout=30)
            and _wait_for_uninstall_entry_absence(install_dir)
        )
        final_uninstall_remaining = _remaining_relative_files(install_dir)
        checks["final_private_data_preserved"] = bool(
            sentinel.is_file() and _sha256(sentinel) == sentinel_hash
        )
        active_uninstaller = None
    finally:
        cleanup_entry = _matching_uninstall_entry(install_dir)
        if cleanup_entry is not None:
            try:
                active_uninstaller = _uninstaller_from_entry(cleanup_entry, install_dir)
            except RuntimeError:
                active_uninstaller = None
        if active_uninstaller is not None and active_uninstaller.is_file():
            try:
                _run(
                    _uninstaller_command(active_uninstaller, report_dir / "cleanup-uninstall.log"),
                    env=env,
                    timeout=args.timeout,
                )
            except (OSError, subprocess.SubprocessError):
                pass
        registry_clean = _wait_for_uninstall_entry_absence(install_dir)
        cleanup_complete = bool(
            registry_clean and _safe_remove_probe_root(root, work_parent)
        )
        checks["temporary_probe_data_removed"] = cleanup_complete

    passed = all(checks.values())
    report = {
        "schema_version": SCHEMA_VERSION,
        "probe": "echo_link_installer_lifecycle",
        "started_at": started_at,
        "finished_at": _utc_now(),
        "passed": passed,
        "checks": checks,
        "installer": {
            "name": installer.name,
            "size_bytes": installer.stat().st_size,
            "sha256": _sha256(installer),
            "signature_status": _signature_status(installer),
        },
        "runs": runs,
        "diagnostics": diagnostics_summary,
        "bundled_dependencies": dependency_inventory,
        "workflow": workflow_summary,
        "repair": repair_summary,
        "first_uninstall": {
            **first_uninstall_summary,
            "remaining_files": first_uninstall_remaining,
        },
        "final_uninstall": {
            **second_uninstall_summary,
            "remaining_files": final_uninstall_remaining,
        },
        "privacy": {
            "isolated_data_directory": True,
            "real_echo_link_data_opened": False,
            "dictated_text_stored_in_evidence": False,
            "clipboard_content_stored_in_evidence": False,
            "private_data_preserved_across_uninstall": checks["private_data_preserved"],
            "temporary_probe_data_removed": cleanup_complete,
        },
        "limitations": [
            "This proves same-machine current-user lifecycle behavior, not a clean Windows machine.",
            "Upgrade and rollback require a separately versioned prior installer.",
            "The installer remains unsuitable for public release until it is code-signed.",
        ],
    }
    _write_report(output, report)
    if not passed:
        failed = [name for name, value in checks.items() if not value]
        raise RuntimeError(f"Installer lifecycle checks failed: {', '.join(failed)}")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify Manuscript install, repair, uninstall, reinstall, and data preservation"
    )
    parser.add_argument(
        "--installer",
        type=Path,
        default=ROOT / "installer-output" / "ManuscriptV2Setup.exe",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs" / "qa" / "installer-lifecycle-evidence.json",
    )
    parser.add_argument("--work-root", type=Path)
    parser.add_argument("--timeout", type=float, default=180.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = run_probe(args)
    except Exception as exc:
        print(f"Installer lifecycle probe failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"passed": report["passed"], "checks": len(report["checks"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
