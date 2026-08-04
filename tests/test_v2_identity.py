from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

from echo_link_app import branding, startup, windows_identity
from echo_link_app.main import build_parser
from echo_link_core import data_protection, database_crypto, paths
from tools import build


class _FakeKey:
    def __init__(self, values: dict[str, str]):
        self.values = values

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _FakeWinReg:
    HKEY_CURRENT_USER = object()
    REG_SZ = 1

    def __init__(self, values: dict[str, str]):
        self.values = values

    def CreateKey(self, _root, _path):
        return _FakeKey(self.values)

    def SetValueEx(self, key, name, _reserved, _kind, value):
        key.values[name] = value

    def DeleteValue(self, key, name):
        if name not in key.values:
            raise FileNotFoundError(name)
        del key.values[name]


def test_v2_defaults_to_its_own_private_data_folder(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("ECHOLINK_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("APPDATA", raising=False)

    assert paths.data_root() == tmp_path / "ManuscriptWindowsV2"


def test_v2_startup_registration_preserves_v1_entries(monkeypatch) -> None:
    values = {
        "EchoLink": '"C:\\Old\\EchoLink.exe"',
        "Manuscript": '"C:\\Program Files\\Manuscript\\Manuscript.exe"',
    }
    fake = _FakeWinReg(values)
    monkeypatch.setattr(startup.os, "name", "nt")
    monkeypatch.setattr(
        startup,
        "startup_command",
        lambda: '"C:\\Program Files\\Manuscript V2\\ManuscriptV2.exe" --startup',
    )
    monkeypatch.setitem(sys.modules, "winreg", fake)

    startup.configure_startup(True)

    assert values == {
        "EchoLink": '"C:\\Old\\EchoLink.exe"',
        "Manuscript": '"C:\\Program Files\\Manuscript\\Manuscript.exe"',
        "ManuscriptWindowsV2": (
            '"C:\\Program Files\\Manuscript V2\\ManuscriptV2.exe" --startup'
        ),
    }


def test_v2_shell_identity_cannot_collide_with_v1() -> None:
    assert branding.APP_NAME == "Manuscript Windows Version 2"
    assert branding.APP_EXECUTABLE == "ManuscriptV2.exe"
    assert branding.APP_USER_MODEL_ID == "Manuscript.WindowsV2"
    assert windows_identity.APP_MUTEX_NAME == (
        "Manuscript.WindowsV2.App.65BD5BC9-6E5E-4E2D-9BB1-B49EA46CB73E"
    )


def test_v2_checkout_is_an_accepted_build_root() -> None:
    build._assert_canonical_project_root(Path(__file__).parents[1])


def test_v2_global_hotkeys_require_explicit_launch_opt_in() -> None:
    assert build_parser().parse_args([]).enable_global_hotkeys is False
    assert (
        build_parser().parse_args(["--enable-global-hotkeys"]).enable_global_hotkeys
        is True
    )


def test_v2_installer_and_browser_host_are_isolated() -> None:
    root = Path(__file__).parents[1]
    installer = (root / "packaging" / "EchoLink.iss").read_text(encoding="utf-8")
    app_spec = (root / "EchoLink.spec").read_text(encoding="utf-8")
    host_spec = (root / "BrowserHost.spec").read_text(encoding="utf-8")
    browser_background = (root / "browser-extension" / "background.js").read_text(
        encoding="utf-8"
    )
    host_installer = (root / "tools" / "install_browser_native_host.py").read_text(
        encoding="utf-8"
    )

    assert '#define MyAppName "Manuscript Windows Version 2"' in installer
    assert '#define MyAppExeName "ManuscriptV2.exe"' in installer
    assert "65BD5BC9-6E5E-4E2D-9BB1-B49EA46CB73E" in installer
    assert "Manuscript.WindowsV2" in installer
    assert "ManuscriptV2Setup" in installer
    assert "com.manuscript.v2.polish" in installer
    assert 'name="ManuscriptV2"' in app_spec
    assert 'name="ManuscriptV2BrowserHost"' in host_spec
    assert '"com.manuscript.v2.polish"' in browser_background
    assert 'HOST_NAME = "com.manuscript.v2.polish"' in host_installer


def test_v2_browser_extension_id_is_distinct_and_consistent() -> None:
    root = Path(__file__).parents[1]
    extension = root / "browser-extension"
    manifest = json.loads((extension / "manifest.json").read_text(encoding="utf-8"))
    raw_key = base64.b64decode(manifest["key"], validate=True)
    digest = hashlib.sha256(raw_key).digest()[:16].hex()
    derived_id = "".join(chr(ord("a") + int(value, 16)) for value in digest)
    committed_id = (extension / "extension-id.txt").read_text(encoding="ascii").strip()
    installer = (root / "packaging" / "EchoLink.iss").read_text(encoding="utf-8")

    assert derived_id != "amjackdejmlebghkojdddodaapfeipam"
    assert committed_id == derived_id
    assert f'#define BrowserExtensionId "{derived_id}"' in installer


def test_v2_specific_path_overrides_win_over_v1_overrides(monkeypatch, tmp_path) -> None:
    (tmp_path / "v1-models").mkdir()
    (tmp_path / "v2-models").mkdir()
    monkeypatch.setenv("ECHOLINK_DATA_DIR", str(tmp_path / "v1-data"))
    monkeypatch.setenv("MANUSCRIPT_V2_DATA_DIR", str(tmp_path / "v2-data"))
    monkeypatch.setenv("ECHOLINK_MODEL_DIR", str(tmp_path / "v1-models"))
    monkeypatch.setenv("MANUSCRIPT_V2_MODEL_DIR", str(tmp_path / "v2-models"))

    assert paths.data_root() == tmp_path / "v2-data"
    assert paths.model_root() == tmp_path / "v2-models"


def test_v2_data_protection_namespace_cannot_collide_with_v1() -> None:
    assert data_protection.PRODUCT_ID == "manuscript-windows-v2"
    assert database_crypto._ENTROPY == hashlib.sha256(
        b"ManuscriptWindowsV2/sqlcipher-key/v1"
    ).digest()


def test_v2_operational_scripts_do_not_target_v1_state() -> None:
    root = Path(__file__).parents[1]
    stability = (root / "scripts" / "stability_gate.ps1").read_text(encoding="utf-8")
    signer = (root / "tools" / "sign_polish_manifest.ps1").read_text(
        encoding="utf-8"
    )
    reliability = (root / "tools" / "correction_reliability_probe.py").read_text(
        encoding="utf-8"
    )

    assert "manuscript-windows-v2-canonical-v1" in stability
    assert '"ManuscriptWindowsV2\\release-keys' in signer
    assert '"Local" / "ManuscriptWindowsV2"' in reliability
