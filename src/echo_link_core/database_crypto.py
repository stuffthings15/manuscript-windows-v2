from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sqlite3 as plain_sqlite
import sys
from contextlib import closing
from ctypes import wintypes
from pathlib import Path
from typing import Any, Iterable

try:
    from sqlcipher3 import dbapi2 as sqlcipher
except ImportError as exc:  # pragma: no cover - exercised by packaged dependency checks
    sqlcipher = None
    _SQLCIPHER_IMPORT_ERROR = exc
else:
    _SQLCIPHER_IMPORT_ERROR = None

from .data_protection import validate_local_data_file


SQLITE_HEADER = b"SQLite format 3\x00"
KEY_FILE_MAGIC = b"ELK1"
KEY_BYTES = 32
CRYPTPROTECT_UI_FORBIDDEN = 0x1
# Keep the legacy entropy forever so existing encrypted profiles remain readable.
_ENTROPY = hashlib.sha256(b"Echo-Link/sqlcipher-key/v1").digest()


class DatabaseEncryptionError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


class EncryptedDatabase:
    """Own SQLCipher setup, DPAPI key files, migration, and key rotation."""

    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)
        self.key_path = self.path.with_name(f"{self.path.stem}.key.dpapi")
        self.pending_key_path = self.path.with_name(f"{self.path.stem}.key.pending.dpapi")
        self.migration_path = self.path.with_name(f".{self.path.name}.encrypted-migration")
        for key_path, purpose in (
            (self.key_path, "Manuscript encrypted database key"),
            (self.pending_key_path, "Manuscript pending encrypted database key"),
        ):
            validate_local_data_file(
                key_path,
                purpose=purpose,
                allowed_suffixes={".dpapi"},
                create_parent=True,
            )
        validate_local_data_file(
            self.migration_path,
            purpose="Manuscript encrypted database migration file",
            allowed_suffixes={".encrypted-migration"},
            create_parent=True,
        )
        self._key: bytes | None = None

    def prepare(self) -> None:
        self._require_runtime()
        self._recover_pending_rotation()
        if not self.path.exists():
            self._key = self._load_or_create_key()
            return
        if _has_plaintext_header(self.path):
            self._key = self._load_or_create_key()
            self._migrate_plaintext_database(self._key)
            return
        if not self.key_path.exists():
            raise DatabaseEncryptionError(
                "Manuscript found an encrypted database without its Windows key file."
            )
        self._key = self._read_key(self.key_path)
        with closing(self._open_with_key(self.path, self._key, wal=False)):
            pass

    def connect(self):
        if self._key is None:
            raise DatabaseEncryptionError("Manuscript encrypted storage is not prepared.")
        return self._open_with_key(self.path, self._key, wal=True)

    def rotate_key(self) -> None:
        if self._key is None:
            raise DatabaseEncryptionError("Manuscript encrypted storage is not prepared.")
        new_key = os.urandom(KEY_BYTES)
        self._write_key(self.pending_key_path, new_key)
        connection = self._open_with_key(self.path, self._key, wal=False)
        try:
            connection.execute(f'PRAGMA rekey = "x\'{new_key.hex()}\'"')
            connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
            integrity = connection.execute("PRAGMA cipher_integrity_check").fetchall()
            if not _cipher_integrity_ok(integrity):
                raise DatabaseEncryptionError("SQLCipher rejected the rotated database key.")
        finally:
            connection.close()
        with closing(self._open_with_key(self.path, new_key, wal=False)):
            pass
        os.replace(self.pending_key_path, self.key_path)
        _flush_file(self.key_path)
        self._key = new_key

    def status(self) -> dict[str, Any]:
        if self._key is None:
            raise DatabaseEncryptionError("Manuscript encrypted storage is not prepared.")
        with closing(self._open_with_key(self.path, self._key, wal=True)) as connection:
            cipher_version = str(connection.execute("PRAGMA cipher_version").fetchone()[0])
            temp_store = int(connection.execute("PRAGMA temp_store").fetchone()[0])
            journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
        return {
            "encrypted": not _has_plaintext_header(self.path),
            "cipher": "SQLCipher",
            "cipher_version": cipher_version,
            "key_backend": "Windows DPAPI current user",
            "key_file_present": self.key_path.is_file(),
            "temp_store_memory": temp_store == 2,
            "journal_mode": journal_mode,
            "secure_erase_claimed": False,
        }

    def _require_runtime(self) -> None:
        if sys.platform != "win32":
            raise DatabaseEncryptionError(
                "Manuscript encrypted database storage currently requires Windows."
            )
        if sqlcipher is None:
            raise DatabaseEncryptionError(
                "Manuscript cannot open private storage because SQLCipher is missing."
            ) from _SQLCIPHER_IMPORT_ERROR
        connection = sqlcipher.connect(":memory:")
        try:
            row = connection.execute("PRAGMA cipher_version").fetchone()
            compile_options = {
                str(item[0]) for item in connection.execute("PRAGMA compile_options").fetchall()
            }
        finally:
            connection.close()
        if not row or not str(row[0]).strip() or "HAS_CODEC" not in compile_options:
            raise DatabaseEncryptionError("The installed SQLite runtime does not contain SQLCipher.")
        if "TEMP_STORE=2" not in compile_options and "TEMP_STORE=3" not in compile_options:
            raise DatabaseEncryptionError(
                "SQLCipher was built without a memory-safe temporary-store default."
            )

    def _load_or_create_key(self) -> bytes:
        if self.key_path.exists():
            return self._read_key(self.key_path)
        key = os.urandom(KEY_BYTES)
        self._write_key(self.key_path, key)
        return key

    def _read_key(self, path: Path) -> bytes:
        try:
            payload = path.read_bytes()
            if not payload.startswith(KEY_FILE_MAGIC):
                raise DatabaseEncryptionError("Manuscript database key has an unknown format.")
            key = _dpapi_unprotect(payload[len(KEY_FILE_MAGIC) :])
        except DatabaseEncryptionError:
            raise
        except Exception as exc:
            raise DatabaseEncryptionError(
                "Manuscript could not unlock its database key for this Windows user."
            ) from exc
        if len(key) != KEY_BYTES:
            raise DatabaseEncryptionError("Manuscript database key is invalid.")
        return key

    def _write_key(self, path: Path, key: bytes) -> None:
        if len(key) != KEY_BYTES:
            raise DatabaseEncryptionError("Manuscript database key is invalid.")
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            protected = _dpapi_protect(key)
            temporary.write_bytes(KEY_FILE_MAGIC + protected)
            _flush_file(temporary)
            os.replace(temporary, path)
            _flush_file(path)
        except Exception as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise DatabaseEncryptionError("Manuscript could not save its database key.") from exc

    def _recover_pending_rotation(self) -> None:
        if not self.pending_key_path.exists():
            return
        if not self.path.exists() or _has_plaintext_header(self.path):
            self.pending_key_path.unlink(missing_ok=True)
            return
        pending = self._read_key(self.pending_key_path)
        try:
            current = self._read_key(self.key_path) if self.key_path.exists() else None
        except DatabaseEncryptionError:
            current = None
        if current is not None and self._key_opens_database(current):
            self.pending_key_path.unlink(missing_ok=True)
            return
        if self._key_opens_database(pending):
            os.replace(self.pending_key_path, self.key_path)
            _flush_file(self.key_path)
            return
        raise DatabaseEncryptionError(
            "Manuscript could not recover an interrupted database-key rotation."
        )

    def _key_opens_database(self, key: bytes) -> bool:
        try:
            with closing(self._open_with_key(self.path, key, wal=False)):
                return True
        except Exception:
            return False

    def _open_with_key(self, path: Path, key: bytes, *, wal: bool):
        if sqlcipher is None:
            raise DatabaseEncryptionError("SQLCipher is unavailable.")
        connection = sqlcipher.connect(path, timeout=10.0)
        try:
            connection.execute(f'PRAGMA key = "x\'{key.hex()}\'"')
            connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
            version = connection.execute("PRAGMA cipher_version").fetchone()
            if not version or not str(version[0]).strip():
                raise DatabaseEncryptionError("SQLCipher did not report a cipher version.")
            connection.execute("PRAGMA temp_store=MEMORY")
            if int(connection.execute("PRAGMA temp_store").fetchone()[0]) != 2:
                raise DatabaseEncryptionError("SQLCipher temporary storage is not memory-only.")
            connection.execute("PRAGMA foreign_keys=ON")
            mode = connection.execute(
                "PRAGMA journal_mode=WAL" if wal else "PRAGMA journal_mode=DELETE"
            ).fetchone()
            expected = "wal" if wal else "delete"
            if not mode or str(mode[0]).casefold() != expected:
                raise DatabaseEncryptionError(f"SQLCipher could not enable {expected} journaling.")
            connection.row_factory = sqlcipher.Row
            return connection
        except Exception as exc:
            connection.close()
            if isinstance(exc, DatabaseEncryptionError):
                raise
            raise DatabaseEncryptionError(
                "Manuscript could not open its encrypted database with the Windows key."
            ) from exc

    def _migrate_plaintext_database(self, key: bytes) -> None:
        self._checkpoint_plaintext_wal()
        self.migration_path.unlink(missing_ok=True)
        source_fingerprint: str
        with closing(plain_sqlite.connect(self.path, timeout=10.0)) as source:
            source_fingerprint = _database_fingerprint(source)
            integrity = source.execute("PRAGMA integrity_check").fetchall()
            if not _integrity_ok(integrity):
                raise DatabaseEncryptionError(
                    "The plaintext Manuscript database failed integrity checks before encryption."
                )

        if sqlcipher is None:
            raise DatabaseEncryptionError("SQLCipher is unavailable.")
        try:
            source = sqlcipher.connect(self.path, timeout=10.0)
            try:
                source.execute("SELECT count(*) FROM sqlite_master").fetchone()
                source.execute("PRAGMA temp_store=MEMORY")
                quoted_path = _sql_string(str(self.migration_path))
                source.execute(
                    f'ATTACH DATABASE {quoted_path} AS encrypted KEY "x\'{key.hex()}\'"'
                )
                source.execute("PRAGMA encrypted.journal_mode=DELETE")
                source.execute("SELECT sqlcipher_export('encrypted')").fetchone()
                source.execute("DETACH DATABASE encrypted")
            finally:
                source.close()

            with closing(self._open_with_key(self.migration_path, key, wal=False)) as encrypted:
                target_fingerprint = _database_fingerprint(encrypted)
                integrity = encrypted.execute("PRAGMA cipher_integrity_check").fetchall()
                if not _cipher_integrity_ok(integrity):
                    raise DatabaseEncryptionError(
                        "The encrypted Manuscript migration failed cipher integrity checks."
                    )
            if target_fingerprint != source_fingerprint:
                raise DatabaseEncryptionError(
                    "The encrypted Manuscript migration did not preserve every schema value."
                )
            _flush_file(self.migration_path)
            os.replace(self.migration_path, self.path)
            _flush_file(self.path)
            _remove_sidecars(self.path)
            with closing(self._open_with_key(self.path, key, wal=False)):
                pass
        except DatabaseEncryptionError:
            self.migration_path.unlink(missing_ok=True)
            raise
        except Exception as exc:
            self.migration_path.unlink(missing_ok=True)
            raise DatabaseEncryptionError(
                "Manuscript could not migrate the plaintext database into SQLCipher."
            ) from exc

    def _checkpoint_plaintext_wal(self) -> None:
        try:
            with closing(plain_sqlite.connect(self.path, timeout=10.0)) as connection:
                checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if checkpoint and int(checkpoint[0]) != 0:
                    raise DatabaseEncryptionError(
                        "The plaintext database is busy and cannot be encrypted yet."
                    )
                mode = connection.execute("PRAGMA journal_mode=DELETE").fetchone()
                if not mode or str(mode[0]).casefold() != "delete":
                    raise DatabaseEncryptionError(
                        "The plaintext WAL could not be closed before encryption."
                    )
        except DatabaseEncryptionError:
            raise
        except plain_sqlite.Error as exc:
            raise DatabaseEncryptionError(
                "Manuscript could not checkpoint the plaintext database before encryption."
            ) from exc
        wal = self.path.with_name(self.path.name + "-wal")
        if wal.exists() and wal.stat().st_size:
            raise DatabaseEncryptionError(
                "The plaintext database WAL still contains data; close other Manuscript processes."
            )


def _has_plaintext_header(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(len(SQLITE_HEADER)) == SQLITE_HEADER
    except OSError as exc:
        raise DatabaseEncryptionError("Manuscript could not inspect its database header.") from exc


def _remove_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _integrity_ok(rows: Iterable[tuple[Any, ...]]) -> bool:
    values = [str(row[0]).casefold() for row in rows if row]
    return bool(values) and all(value == "ok" for value in values)


def _cipher_integrity_ok(rows: Iterable[tuple[Any, ...]]) -> bool:
    # SQLCipher returns no rows when every encrypted page is valid.
    return not [row for row in rows if row and str(row[0]).strip()]


def _database_fingerprint(connection) -> str:
    digest = hashlib.sha256()
    objects = connection.execute(
        """
        SELECT type, name, tbl_name, COALESCE(sql, '')
        FROM sqlite_master
        WHERE name NOT LIKE 'sqlite_%'
        ORDER BY type, name
        """
    ).fetchall()
    _hash_value(digest, [tuple(row) for row in objects])
    tables = [str(row[1]) for row in objects if str(row[0]) == "table"]
    for table in sorted(tables):
        identifier = _sql_identifier(table)
        columns = connection.execute(f"PRAGMA table_info({identifier})").fetchall()
        _hash_value(digest, [tuple(row) for row in columns])
        rows = connection.execute(f"SELECT * FROM {identifier}").fetchall()
        encoded = [_encoded_value(tuple(row)) for row in rows]
        for payload in sorted(encoded):
            digest.update(len(payload).to_bytes(8, "big"))
            digest.update(payload)
    sequence_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'"
    ).fetchone()
    if sequence_exists:
        sequence = connection.execute("SELECT name, seq FROM sqlite_sequence ORDER BY name").fetchall()
        _hash_value(digest, [tuple(row) for row in sequence])
    return digest.hexdigest()


def _sql_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _hash_value(digest: Any, value: Any) -> None:
    payload = _encoded_value(value)
    digest.update(len(payload).to_bytes(8, "big"))
    digest.update(payload)


def _encoded_value(value: Any) -> bytes:
    if value is None:
        tagged = ["null", None]
    elif isinstance(value, bool):
        tagged = ["bool", value]
    elif isinstance(value, int):
        tagged = ["int", str(value)]
    elif isinstance(value, float):
        tagged = ["float", value.hex()]
    elif isinstance(value, bytes):
        tagged = ["bytes", value.hex()]
    elif isinstance(value, str):
        tagged = ["text", value]
    elif isinstance(value, (list, tuple)):
        tagged = ["list", [json.loads(_encoded_value(item)) for item in value]]
    else:
        raise DatabaseEncryptionError(
            f"Unsupported SQLite value during encrypted migration: {type(value).__name__}."
        )
    return json.dumps(tagged, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _flush_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDWR)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _blob(payload: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(payload, max(1, len(payload)))
    return (
        _DataBlob(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))),
        buffer,
    )


def _dpapi_protect(payload: bytes) -> bytes:
    if sys.platform != "win32":
        raise DatabaseEncryptionError("Windows DPAPI is unavailable.")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    source, source_buffer = _blob(payload)
    entropy, entropy_buffer = _blob(_ENTROPY)
    output = _DataBlob()
    _ = (source_buffer, entropy_buffer)
    if not crypt32.CryptProtectData(
        ctypes.byref(source),
        "Manuscript SQLCipher key",
        ctypes.byref(entropy),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        if output.pbData:
            kernel32.LocalFree(output.pbData)


def _dpapi_unprotect(payload: bytes) -> bytes:
    if sys.platform != "win32":
        raise DatabaseEncryptionError("Windows DPAPI is unavailable.")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    source, source_buffer = _blob(payload)
    entropy, entropy_buffer = _blob(_ENTROPY)
    output = _DataBlob()
    description = wintypes.LPWSTR()
    _ = (source_buffer, entropy_buffer)
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source),
        ctypes.byref(description),
        ctypes.byref(entropy),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        if output.pbData:
            kernel32.LocalFree(output.pbData)
        if description:
            kernel32.LocalFree(description)
