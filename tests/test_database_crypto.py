from __future__ import annotations

import os
import sqlite3
from contextlib import closing

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from echo_link_core.database_crypto import (
    KEY_FILE_MAGIC,
    SQLITE_HEADER,
    DatabaseEncryptionError,
    EncryptedDatabase,
)
from echo_link_core.storage import EchoLinkStore


def test_new_database_and_wal_are_encrypted_with_dpapi_wrapped_key(tmp_path) -> None:
    path = tmp_path / "echo-link.db"
    store = EchoLinkStore(path)
    sentinel = "PRIVATE_DATABASE_AND_WAL_SENTINEL"

    connection = store._database.connect()
    try:
        connection.execute(
            "INSERT INTO snippets(spoken_form, content, created_at) VALUES(?, ?, ?)",
            ("secret", sentinel, "2026-07-19T00:00:00+00:00"),
        )
        connection.commit()
        wal_path = path.with_name(path.name + "-wal")
        assert wal_path.is_file()
        assert sentinel.encode() not in wal_path.read_bytes()
    finally:
        connection.close()

    key_path = store._database.key_path
    raw_key = store._database._key
    assert raw_key is not None
    assert not path.read_bytes().startswith(SQLITE_HEADER)
    assert sentinel.encode() not in path.read_bytes()
    assert key_path.read_bytes().startswith(KEY_FILE_MAGIC)
    assert raw_key not in key_path.read_bytes()
    with pytest.raises(sqlite3.DatabaseError):
        with closing(sqlite3.connect(path)) as plaintext:
            plaintext.execute("SELECT count(*) FROM sqlite_master").fetchone()

    status = store.database_security_status()
    assert status["encrypted"] is True
    assert status["cipher"] == "SQLCipher"
    assert status["cipher_version"]
    assert status["key_backend"] == "Windows DPAPI current user"
    assert status["temp_store_memory"] is True
    assert status["journal_mode"] == "wal"
    assert status["secure_erase_claimed"] is False


def test_plaintext_database_is_migrated_without_schema_or_value_loss(tmp_path) -> None:
    path = tmp_path / "echo-link.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE custom(
                id INTEGER PRIMARY KEY,
                phrase TEXT UNIQUE,
                payload BLOB,
                score REAL
            );
            CREATE INDEX custom_phrase_idx ON custom(phrase);
            CREATE TRIGGER custom_no_delete
            BEFORE DELETE ON custom
            BEGIN
                SELECT RAISE(ABORT, 'custom rows stay');
            END;
            """
        )
        connection.execute(
            "INSERT INTO custom(phrase, payload, score) VALUES(?, ?, ?)",
            ("PLAINTEXT_MIGRATION_SENTINEL", b"\x00\x01\xff", 1.25),
        )
        connection.commit()

    assert path.read_bytes().startswith(SQLITE_HEADER)
    store = EchoLinkStore(path)
    assert not path.read_bytes().startswith(SQLITE_HEADER)
    assert b"PLAINTEXT_MIGRATION_SENTINEL" not in path.read_bytes()
    assert not store._database.migration_path.exists()
    with store._connect() as connection:
        row = connection.execute(
            "SELECT phrase, payload, score FROM custom"
        ).fetchone()
        objects = {
            (str(item[0]), str(item[1]))
            for item in connection.execute(
                "SELECT type, name FROM sqlite_master WHERE name LIKE 'custom_%'"
            ).fetchall()
        }
    assert tuple(row) == ("PLAINTEXT_MIGRATION_SENTINEL", b"\x00\x01\xff", 1.25)
    assert ("index", "custom_phrase_idx") in objects
    assert ("trigger", "custom_no_delete") in objects


def test_missing_or_corrupt_dpapi_key_fails_closed(tmp_path) -> None:
    path = tmp_path / "echo-link.db"
    store = EchoLinkStore(path)
    key_path = store._database.key_path
    key_path.unlink()
    with pytest.raises(DatabaseEncryptionError, match="without its Windows key file"):
        EchoLinkStore(path)

    path2 = tmp_path / "corrupt.db"
    store2 = EchoLinkStore(path2)
    store2._database.key_path.write_bytes(b"not-a-dpapi-key")
    with pytest.raises(DatabaseEncryptionError, match="unknown format"):
        EchoLinkStore(path2)


def test_interrupted_key_rotation_recovers_both_safe_states(tmp_path) -> None:
    path = tmp_path / "echo-link.db"
    store = EchoLinkStore(path)
    database = store._database

    unused_pending = os.urandom(32)
    database._write_key(database.pending_key_path, unused_pending)
    recovered_old = EncryptedDatabase(path)
    recovered_old.prepare()
    assert not recovered_old.pending_key_path.exists()
    assert recovered_old._key == database._key

    old_key = recovered_old._key
    assert old_key is not None
    new_key = os.urandom(32)
    recovered_old._write_key(recovered_old.pending_key_path, new_key)
    connection = recovered_old._open_with_key(path, old_key, wal=False)
    try:
        connection.execute(f'''PRAGMA rekey = "x'{new_key.hex()}'"''')
        connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
    finally:
        connection.close()

    recovered_new = EncryptedDatabase(path)
    recovered_new.prepare()
    assert recovered_new._key == new_key
    assert not recovered_new.pending_key_path.exists()
    with closing(recovered_new.connect()) as connection:
        assert connection.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] > 0


def test_private_data_purge_rotates_active_database_key(tmp_path) -> None:
    store = EchoLinkStore(tmp_path / "echo-link.db")
    store.add_history("private raw", "Private final", "fast")
    old_key = store._database._key
    assert old_key is not None

    result = store.purge_private_data()

    assert result.active_database_key_rotated is True
    assert store._database._key != old_key
    assert store.list_history() == []
    connection = sqlcipher.connect(store.path)
    try:
        connection.execute(f'''PRAGMA key = "x'{old_key.hex()}'"''')
        with pytest.raises(sqlcipher.DatabaseError):
            connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
    finally:
        connection.close()


def test_key_protection_never_requests_machine_scope() -> None:
    import echo_link_core.database_crypto as database_crypto

    assert not hasattr(database_crypto, "CRYPTPROTECT_LOCAL_MACHINE")
