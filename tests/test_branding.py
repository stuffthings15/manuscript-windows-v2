from __future__ import annotations

import hashlib
from pathlib import Path

from echo_link_app.branding import (
    APP_EXECUTABLE,
    APP_NAME,
    APP_TAGLINE,
    APP_USER_MODEL_ID,
    ICON_ICO,
    ICON_PNG,
)
from echo_link_core import database_crypto
from echo_link_core.paths import APP_NAME as LEGACY_DATA_FOLDER_NAME


ROOT = Path(__file__).resolve().parents[1]


def test_public_windows_brand_is_manuscript() -> None:
    assert APP_NAME == "Manuscript"
    assert APP_TAGLINE == "Flawless writing powered locally"
    assert APP_EXECUTABLE == "Manuscript.exe"
    assert APP_USER_MODEL_ID == "EchoLink.Desktop"
    assert ROOT.joinpath(*ICON_PNG).is_file()
    assert ROOT.joinpath(*ICON_ICO).is_file()


def test_user_facing_windows_source_has_no_old_brand() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "src" / "echo_link_app").glob("*.py"))
    )
    assert "Echo-Link" not in source
    assert "Fast, private voice-to-text for Windows" not in source


def test_rebrand_keeps_existing_encrypted_profile_compatible() -> None:
    assert LEGACY_DATA_FOLDER_NAME == "Echo-Link"
    assert database_crypto._ENTROPY == hashlib.sha256(
        b"Echo-Link/sqlcipher-key/v1"
    ).digest()
