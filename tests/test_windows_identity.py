from __future__ import annotations

from echo_link_app import windows_identity


def test_windows_identity_matches_installer_contract() -> None:
    assert windows_identity.APP_USER_MODEL_ID == "EchoLink.Desktop"
    assert windows_identity.APP_MUTEX_NAME == (
        "EchoLink.App.71099095-BCB5-4E9C-A623-D0C474335542"
    )


def test_non_windows_identity_helpers_are_noops(monkeypatch) -> None:
    monkeypatch.setattr(windows_identity.sys, "platform", "linux")

    windows_identity.configure_windows_app_identity()
    assert windows_identity.create_installer_mutex() is None
    windows_identity.close_installer_mutex(None)
