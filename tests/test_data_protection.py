from __future__ import annotations

import json
from pathlib import Path

import pytest

from echo_link_core import data_protection as protection
from echo_link_core.data_protection import (
    LocalExportGuard,
    PERSISTED_SECRET_FIELDS,
    UnsafeExportError,
    UnsafePathError,
    audit_persisted_text_schema,
)
from echo_link_core.storage import EchoLinkStore


def test_structured_export_has_manifest_redacts_secrets_and_denies_audio(tmp_path):
    destination = tmp_path / "report.json"
    receipt = LocalExportGuard().write_json(
        destination,
        "privacy-test",
        {
            "result": "Bearer PRIVATE_TOKEN_12345",
            "api_key": "PRIVATE_API_KEY_12345",
        },
        contains_user_text=True,
    )

    rendered = destination.read_text(encoding="utf-8")
    payload = json.loads(rendered)
    assert "PRIVATE_TOKEN_12345" not in rendered
    assert "PRIVATE_API_KEY_12345" not in rendered
    assert payload["api_key"] == "[REDACTED]"
    assert payload["export_manifest"]["product_id"] == "echo-link"
    assert payload["export_manifest"]["redaction_applied"] is True
    assert payload["export_manifest"]["contains_raw_audio"] is False
    assert receipt.manifest.contains_persisted_secrets is False

    with pytest.raises(UnsafeExportError, match="Raw audio"):
        LocalExportGuard().prepare_json(
            "privacy-test",
            {"raw_audio": [1, 2, 3]},
            contains_user_text=False,
        )
    with pytest.raises(UnsafeExportError, match="Binary"):
        LocalExportGuard().prepare_json(
            "privacy-test",
            {"payload": b"audio"},
            contains_user_text=False,
        )


def test_local_destinations_deny_unc_mapped_and_linked_parents(tmp_path, monkeypatch):
    guard = LocalExportGuard()
    with pytest.raises(UnsafePathError, match="Network"):
        guard.write_json(
            r"\\server\share\report.json",
            "privacy-test",
            {},
            contains_user_text=False,
        )
    with pytest.raises(UnsafePathError, match="Network"):
        EchoLinkStore(r"\\server\share\echo-link.db")

    mapped = tmp_path / "mapped.json"
    original_network_check = protection.is_network_path
    monkeypatch.setattr(
        protection,
        "is_network_path",
        lambda value: Path(value) == mapped.absolute() or original_network_check(value),
    )
    with pytest.raises(UnsafePathError, match="Network"):
        guard.write_json(mapped, "privacy-test", {}, contains_user_text=False)
    assert not mapped.exists()

    monkeypatch.setattr(protection, "is_network_path", original_network_check)
    linked_parent = tmp_path / "linked-parent"
    linked_parent.mkdir()
    original_link_check = protection._is_link
    monkeypatch.setattr(
        protection,
        "_is_link",
        lambda value: Path(value) == linked_parent or original_link_check(value),
    )
    with pytest.raises(UnsafePathError, match="Symbolic-link"):
        guard.write_json(
            linked_parent / "report.json",
            "privacy-test",
            {},
            contains_user_text=False,
        )


def test_schema_inventory_has_no_secret_or_audio_fields_and_rejects_new_boundaries(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    assert PERSISTED_SECRET_FIELDS == frozenset()
    assert store.get_settings().keep_history is True
    assert store.get_settings().history_retention_days == 3
    assert store.get_settings().keep_recovery_text is False

    with store._connect() as connection:
        assert audit_persisted_text_schema(connection) == []
        connection.execute(
            "CREATE TABLE future_private_data(id INTEGER, api_key TEXT, audio_blob BLOB)"
        )
        failures = audit_persisted_text_schema(connection)

    combined = "\n".join(failures)
    assert "Unreviewed persisted table: future_private_data" in combined
    assert "Unprotected secret field in future_private_data.api_key" in combined
    assert "Raw-audio field in future_private_data.audio_blob" in combined
    assert "Unreviewed BLOB field in future_private_data.audio_blob" in combined
    assert not any(path.suffix.casefold() in {".wav", ".mp3", ".flac", ".ogg"} for path in tmp_path.rglob("*"))
