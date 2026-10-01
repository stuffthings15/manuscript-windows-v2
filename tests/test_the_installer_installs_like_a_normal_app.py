"""Manuscript must install the way any other Windows program installs.

Curtis reported on 2026-09-24 that the app "is not available to be uninstalled
in the Control Panel under Uninstall". That was accurate, and it was not a bug
in the installer script so much as an unexamined default: the baseline commit
that created this project shipped PrivilegesRequired=lowest and a
{localappdata} install directory, and nothing since had revisited it.

A per-user install registers its uninstaller under HKCU. Windows 11 shows those
in Settings -> Apps -> Installed apps, but the Control Panel "Programs and
Features" applet reads the machine hive, so the app was genuinely absent from
the place people look for it. The app was installed, registered and
uninstallable the whole time; it just was not where a user would find it.

These freeze the machine-wide shape so it cannot drift back.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "packaging" / "EchoLink.iss"


def _installer_text() -> str:
    return INSTALLER.read_text(encoding="utf-8")


def test_the_uninstaller_lands_in_the_machine_hive_where_control_panel_looks():
    """PrivilegesRequired decides which hive Inno writes the uninstaller into.

    This single line is the whole reported defect. With "lowest" the entry goes
    to HKCU and Control Panel never shows it.
    """
    assert "PrivilegesRequired=admin" in _installer_text()
    assert "PrivilegesRequired=lowest" not in _installer_text()


def test_the_app_installs_to_program_files_not_one_users_profile():
    """{autopf} resolves to Program Files for an admin install.

    Safe only because nothing writes into the install directory at runtime:
    data_root() is under LOCALAPPDATA per user, and model packs install to
    user_model_root(). _safe_bundled_root() reads the bundle with create=False.
    If that ever changes, a non-admin user hits an access denied error that
    will not look like a packaging problem, so it is asserted here.
    """
    text = _installer_text()
    assert r"DefaultDirName={autopf}\Manuscript Windows Version 2" in text
    assert r"{localappdata}\Programs" not in text


def test_the_browser_host_is_registered_for_every_user_on_the_machine():
    """A machine-wide install must not register the host in one user's hive.

    The installer runs elevated, so HKCU would be the ADMIN's hive - not
    necessarily the person who then uses the app. Every other account would get
    an extension that connects to nothing.
    """
    text = _installer_text()
    for browser in (r"Google\Chrome", r"Microsoft\Edge"):
        needle = r'Root: HKLM; Subkey: "Software' + "\\" + browser + "\\" + "NativeMessagingHosts"
        assert needle in text
    assert "Root: HKCU; Subkey" not in text


def test_the_uninstaller_still_names_itself_for_the_uninstall_list():
    """Without these the Control Panel row is blank or shows a raw path."""
    text = _installer_text()
    assert "UninstallDisplayName={#MyAppName}" in text
    assert r"UninstallDisplayIcon={app}\{#MyAppExeName}" in text
