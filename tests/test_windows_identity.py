from __future__ import annotations

from echo_link_app import windows_identity


def test_windows_identity_matches_installer_contract() -> None:
    assert windows_identity.APP_USER_MODEL_ID == "Manuscript.WindowsV2"
    assert windows_identity.APP_MUTEX_NAME == (
        "Manuscript.WindowsV2.App.65BD5BC9-6E5E-4E2D-9BB1-B49EA46CB73E"
    )


def test_non_windows_identity_helpers_are_noops(monkeypatch) -> None:
    monkeypatch.setattr(windows_identity.sys, "platform", "linux")

    windows_identity.configure_windows_app_identity()
    assert windows_identity.create_installer_mutex() is None
    windows_identity.close_installer_mutex(None)
