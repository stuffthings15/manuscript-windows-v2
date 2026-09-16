from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from tools import build as build_tool


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT.parent.parent


def test_build_dependency_pins_match_the_audited_requirements() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    build_dependencies = project["project"]["optional-dependencies"]["build"]
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()

    assert "Pillow==12.3.0" in build_dependencies
    assert "Pillow==12.3.0" in requirements

def test_source_launcher_prefers_the_manuscript_source_tree(
    tmp_path: Path,
) -> None:
    probe = (
        "import runpy; "
        f"runpy.run_path({str(ROOT / 'run_manuscript.py')!r}, "
        "run_name='launcher_probe'); "
        "import echo_link_app; "
        "print(echo_link_app.__file__)"
    )
    env = {
        **os.environ,
        "LOCALAPPDATA": str(tmp_path / "local"),
        "APPDATA": str(tmp_path / "roaming"),
    }

    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT.parent.parent,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    imported = Path(result.stdout.strip()).resolve()
    assert imported.is_relative_to((ROOT / "src").resolve())


def test_source_launcher_refuses_a_noncanonical_copy(tmp_path: Path) -> None:
    copied_launcher = tmp_path / "run_manuscript.py"
    copied_launcher.write_text(
        (ROOT / "run_manuscript.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(copied_launcher)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "canonical Manuscript/Windows" in (result.stdout + result.stderr)


def test_repository_root_build_entrypoint_is_blocked() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(REPOSITORY_ROOT / "tools" / "build.py"),
            "--dry-run",
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "canonical Manuscript/Windows" in (result.stdout + result.stderr)


def test_spec_is_standalone_and_bundles_local_model():
    text = (ROOT / "EchoLink.spec").read_text(encoding="utf-8")
    assert 'name="ManuscriptV2"' in text
    assert '"manuscript.ico"' in text
    assert '"manuscript_version_info.txt"' in text
    assert '"models/base.en"' in text
    assert "VEdit-AI" not in text
    assert "gui_main" not in text
    assert '"build" / "generated" / "subscription.json"' in text
    assert '"resources" / "subscription.json"' not in text


def test_v2_build_never_targets_v1_distribution_folder() -> None:
    text = (ROOT / "tools" / "build.py").read_text(encoding="utf-8")

    assert 'ROOT / "dist" / "Manuscript"' not in text
    assert 'ROOT / "dist" / "ManuscriptV2"' in text


def test_installer_has_safe_unique_identity():
    text = (ROOT / "packaging" / "EchoLink.iss").read_text(encoding="utf-8")
    assert "ManuscriptV2Setup" in text
    assert "65BD5BC9-6E5E-4E2D-9BB1-B49EA46CB73E" in text
    assert "PrivilegesRequired=lowest" in text
    assert "Manuscript" in text
    assert 'AppMutex={#MyAppMutex}' in text
    assert 'AppUserModelID: "{#MyAppUserModelID}"' in text
    assert 'Manuscript.WindowsV2' in text
    assert 'MyAppMutex "Manuscript.WindowsV2.App.65BD5BC9-6E5E-4E2D-9BB1-B49EA46CB73E"' in text
    assert 'Name: "{app}\\EchoLink.exe"' not in text
    assert "Echo-Link.lnk" not in text
    assert "will keep your local private data" in text
    assert "UninstallDisplayName={#MyAppName}" in text
    assert "(not UninstallSilent)" in text
    assert "[UninstallDelete]" not in text


def test_installer_shows_the_eula_before_install_can_proceed() -> None:
    text = (ROOT / "packaging" / "EchoLink.iss").read_text(encoding="utf-8")
    assert "LicenseFile=..\\resources\\EULA.txt" in text

    eula = (ROOT / "resources" / "EULA.txt").read_text(encoding="utf-8")
    assert "END USER LICENSE AGREEMENT" in eula
    assert "I accept the agreement" in eula


def test_windows_file_metadata_uses_public_manuscript_brand() -> None:
    text = (ROOT / "packaging" / "manuscript_version_info.txt").read_text(
        encoding="utf-8"
    )
    assert "ProductName', 'Manuscript Windows Version 2'" in text
    assert "FileDescription', 'Manuscript Windows Version 2 - Flawless writing powered locally'" in text
    assert "OriginalFilename', 'ManuscriptV2.exe'" in text


def test_runtime_does_not_import_legacy_application_modules():
    runtime = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "src").rglob("*.py"))
    )
    for forbidden in ("app.gui_main", "ui.generate_panel", "from app", "from ui"):
        assert forbidden not in runtime


def test_required_runtime_is_bundled_without_first_run_download() -> None:
    spec = (ROOT / "EchoLink.spec").read_text(encoding="utf-8")
    build = (ROOT / "tools" / "build.py").read_text(encoding="utf-8")
    assert '"models/base.en"' in spec
    assert 'collect_dynamic_libs("ctranslate2")' in spec
    assert 'collect_dynamic_libs("sounddevice")' in spec
    assert 'collect_dynamic_libs("sqlcipher3")' in spec
    assert "Bundled speech model is missing" in build


def test_build_refuses_a_noncanonical_project_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    wrong_root = tmp_path / "Echo-Link"
    wrong_root.mkdir()
    monkeypatch.setattr(build_tool, "ROOT", wrong_root)
    monkeypatch.setattr(
        build_tool.importlib.util,
        "find_spec",
        lambda _name: object(),
    )

    with pytest.raises(SystemExit, match="canonical Manuscript.Windows"):
        build_tool.main(["--dry-run"])


def test_build_commit_walks_to_the_parent_repository(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    nested = repository / "Manuscript" / "Windows"
    ref = repository / ".git" / "refs" / "heads" / "main"
    nested.mkdir(parents=True)
    ref.parent.mkdir(parents=True)
    (repository / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    commit = "abcdef1234567890abcdef1234567890abcdef12"
    ref.write_text(f"{commit}\n", encoding="utf-8")

    assert build_tool._read_build_commit(nested) == commit


def test_production_build_rejects_placeholder_entitlement_origin(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "project"
    resources = root / "resources"
    resources.mkdir(parents=True)
    (resources / "subscription.json").write_text(
        '{"origin":"https://replace-before-release.invalid"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(build_tool, "ROOT", root)

    with pytest.raises(SystemExit, match="public HTTPS subscription origin"):
        build_tool._write_subscription_manifest()

    testing_path = build_tool._write_subscription_manifest(testing_unlocked=True)
    assert json.loads(testing_path.read_text(encoding="utf-8")) == {
        "origin": "https://replace-before-release.invalid",
        "subscription_required": False,
        "build_channel": "testing-unlocked",
    }


@pytest.mark.parametrize(
    "origin",
    [
        "http://manuscript.example.com",
        "https://localhost",
        "https://127.0.0.1",
        "https://service.local",
        "https://user@manuscript.example.com",
        "https://manuscript.example.com/path",
        "https://manuscript.example.com:invalid",
    ],
)
def test_production_build_rejects_nonpublic_or_malformed_origins(
    tmp_path: Path,
    monkeypatch,
    origin: str,
) -> None:
    root = tmp_path / "project"
    resources = root / "resources"
    resources.mkdir(parents=True)
    (resources / "subscription.json").write_text(
        json.dumps({"origin": origin}),
        encoding="utf-8",
    )
    monkeypatch.setattr(build_tool, "ROOT", root)

    with pytest.raises(SystemExit, match="public HTTPS subscription origin"):
        build_tool._write_subscription_manifest()


def test_production_build_accepts_public_https_entitlement_origin(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "project"
    resources = root / "resources"
    resources.mkdir(parents=True)
    origin = "https://manuscript-writing.seventhree170.chatgpt.site"
    (resources / "subscription.json").write_text(
        json.dumps({"origin": origin}),
        encoding="utf-8",
    )
    monkeypatch.setattr(build_tool, "ROOT", root)

    result = json.loads(
        build_tool._write_subscription_manifest().read_text(encoding="utf-8")
    )
    assert result == {
        "origin": origin,
        "subscription_required": True,
        "build_channel": "production",
    }

def test_build_parser_accepts_a_code_signing_thumbprint() -> None:
    try:
        args = build_tool.parser().parse_args(
            ["--signing-thumbprint", "AA BB CC DD"]
        )
    except SystemExit:
        args = None

    assert args is not None
    assert args.signing_thumbprint == "AA BB CC DD"


def test_production_build_requires_signing_but_testing_build_does_not() -> None:
    resolve = getattr(build_tool, "_release_signing_thumbprint", None)
    assert callable(resolve)

    with pytest.raises(SystemExit, match="trusted code-signing certificate"):
        resolve("", testing_unlocked=False, environ={})
    assert resolve("", testing_unlocked=True, environ={}) is None
    assert (
        resolve(
            "aa bb cc dd ee ff 00 11 22 33 44 55 66 77 88 99 aa bb cc dd",
            testing_unlocked=False,
            environ={},
        )
        == "AABBCCDDEEFF00112233445566778899AABBCCDD"
    )


def test_release_signing_covers_every_packaged_pe_file(tmp_path: Path) -> None:
    discover = getattr(build_tool, "_signable_files", None)
    assert callable(discover)
    for relative in (
        "Manuscript.exe",
        "browser-host/ManuscriptBrowserHost.exe",
        "_internal/runtime.dll",
        "_internal/plugin.pyd",
        "readme.txt",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test")

    assert [
        path.relative_to(tmp_path).as_posix()
        for path in discover(tmp_path)
    ] == [
        "Manuscript.exe",
        "_internal/plugin.pyd",
        "_internal/runtime.dll",
        "browser-host/ManuscriptBrowserHost.exe",
    ]


def test_signing_uses_sha256_timestamping_and_trust_verification() -> None:
    sign_command = getattr(build_tool, "_sign_command", None)
    verify_command = getattr(build_tool, "_verify_signature_command", None)
    assert callable(sign_command)
    assert callable(verify_command)
    tool = Path("C:/Windows Kits/signtool.exe")
    artifact = Path("C:/release/Manuscript.exe")
    thumbprint = "A" * 40

    assert sign_command(tool, thumbprint, artifact) == [
        str(tool),
        "sign",
        "/sha1",
        thumbprint,
        "/fd",
        "SHA256",
        "/td",
        "SHA256",
        "/tr",
        "https://timestamp.digicert.com",
        str(artifact),
    ]
    assert verify_command(tool, artifact) == [
        str(tool),
        "verify",
        "/pa",
        "/all",
        str(artifact),
    ]
