from __future__ import annotations

import json
import sqlite3 as plain_sqlite
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from sqlcipher3 import dbapi2 as sqlite3

from .database_crypto import EncryptedDatabase
from .data_protection import validate_local_data_file
from .learning import MAX_RULES, normalize_learning_key, validate_learning_rule
from .models import DictationSettings
from .paths import data_root


SCHEMA_VERSION = 8
MAX_SPEECH_LOG_RETENTION_DAYS = 15
MAX_SPEECH_LOG_AUDIO_BYTES = 32 * 1024 * 1024


class DocumentRevisionConflictError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PrivateDataPurgeResult:
    counts: dict[str, int]
    defaults_restored: dict[str, int]
    database_status: str
    wal_checkpoint_status: str
    vacuum_status: str
    product_log_status: str
    product_log_files_cleared: int
    active_database_key_rotated: bool = False
    secure_erase_claimed: bool = False

    @property
    def deleted_row_count(self) -> int:
        return sum(self.counts.values())


class EchoLinkStore:
    def __init__(self, database_path: str | Path | None = None):
        self.path = validate_local_data_file(
            Path(database_path) if database_path else data_root() / "echo-link.db",
            purpose="Manuscript database",
            allowed_suffixes={".db"},
            create_parent=True,
        )
        self._lock = threading.RLock()
        self._database = EncryptedDatabase(self.path)
        self._database.prepare()
        self.initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = self._database.connect()
        try:
            yield connection
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()

    def database_security_status(self) -> dict[str, Any]:
        return self._database.status()

    def initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings(
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS vocabulary(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    spoken_form TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    written_form TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'custom',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dictation_history(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    raw_text TEXT NOT NULL,
                    final_text TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    window_title TEXT NOT NULL DEFAULT '',
                    latency_ms INTEGER,
                    inserted INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS speech_improvement_logs(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    raw_text TEXT NOT NULL,
                    deterministic_text TEXT NOT NULL,
                    suggested_text TEXT NOT NULL,
                    final_text TEXT NOT NULL,
                    audio_pcm BLOB NOT NULL,
                    sample_rate INTEGER NOT NULL CHECK(sample_rate BETWEEN 8000 AND 192000),
                    duration_ms INTEGER NOT NULL CHECK(duration_ms >= 0),
                    mode TEXT NOT NULL,
                    window_title TEXT NOT NULL DEFAULT '',
                    latency_ms INTEGER,
                    inserted INTEGER NOT NULL DEFAULT 0 CHECK(inserted IN (0, 1)),
                    status TEXT NOT NULL DEFAULT 'complete',
                    review_decision TEXT NOT NULL DEFAULT '',
                    punctuation_wrong INTEGER NOT NULL DEFAULT 0 CHECK(punctuation_wrong IN (0, 1)),
                    user_notes TEXT NOT NULL DEFAULT '',
                    analysis_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_speech_improvement_logs_expires
                    ON speech_improvement_logs(expires_at);
                CREATE TABLE IF NOT EXISTS review_feedback(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision TEXT NOT NULL,
                    edited INTEGER NOT NULL DEFAULT 0,
                    mode TEXT NOT NULL,
                    latency_ms INTEGER,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS learned_corrections(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_text TEXT NOT NULL,
                    source_key TEXT NOT NULL UNIQUE,
                    replacement_text TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
                    origin TEXT NOT NULL DEFAULT 'review' CHECK(origin IN ('review', 'manual')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS snippets(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    spoken_form TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recovery_items(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    final_text TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    error_message TEXT NOT NULL DEFAULT '',
                    session_key TEXT,
                    status TEXT NOT NULL DEFAULT 'failed',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    current_revision INTEGER NOT NULL DEFAULT 1 CHECK(current_revision >= 1),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS document_revisions(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,
                    revision_number INTEGER NOT NULL CHECK(revision_number >= 1),
                    title TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    label TEXT NOT NULL,
                    restored_from_revision INTEGER CHECK(
                        restored_from_revision IS NULL OR restored_from_revision >= 1
                    ),
                    created_at TEXT NOT NULL,
                    UNIQUE(document_id, revision_number),
                    FOREIGN KEY(document_id, restored_from_revision)
                        REFERENCES document_revisions(document_id, revision_number)
                        ON DELETE RESTRICT
                );
                CREATE INDEX IF NOT EXISTS idx_document_revisions_document
                    ON document_revisions(document_id, revision_number DESC);
                CREATE TRIGGER IF NOT EXISTS document_revisions_immutable_update
                BEFORE UPDATE ON document_revisions
                BEGIN
                    SELECT RAISE(ABORT, 'Document revisions are immutable.');
                END;
                CREATE TRIGGER IF NOT EXISTS document_revisions_immutable_delete
                BEFORE DELETE ON document_revisions
                BEGIN
                    SELECT RAISE(ABORT, 'Document revisions are immutable.');
                END;
                CREATE TABLE IF NOT EXISTS evaluation_runs(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    commit_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    started_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS evaluation_trials(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES evaluation_runs(id) ON DELETE CASCADE,
                    case_id TEXT NOT NULL,
                    trial INTEGER NOT NULL,
                    decision TEXT NOT NULL DEFAULT 'recording',
                    edited INTEGER NOT NULL DEFAULT 0,
                    latency_ms INTEGER,
                    final_text TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    started_at TEXT NOT NULL,
                    output_ready_at TEXT,
                    accepted_at TEXT,
                    UNIQUE(run_id, case_id, trial)
                );
                """
            )
            _ensure_column(connection, "recovery_items", "session_key", "TEXT")
            _ensure_column(
                connection,
                "recovery_items",
                "status",
                "TEXT NOT NULL DEFAULT 'failed'",
            )
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            existing_settings = connection.execute(
                "SELECT 1 FROM settings WHERE key='dictation'"
            ).fetchone()
            defaults = DictationSettings().to_dict()
            connection.execute(
                "INSERT OR IGNORE INTO settings(key, value_json) VALUES('dictation', ?)",
                (json.dumps(defaults, sort_keys=True),),
            )
            if existing_settings is None:
                connection.execute(
                    "INSERT OR IGNORE INTO schema_meta(key, value) VALUES('first_run_setup_pending', 'true')"
                )
            for spoken, written, category in _legacy_default_vocabulary():
                connection.execute(
                    """
                    DELETE FROM vocabulary
                    WHERE spoken_form=? COLLATE NOCASE
                      AND written_form=?
                      AND category=?
                    """,
                    (spoken, written, category),
                )
            settings_row = connection.execute(
                "SELECT value_json FROM settings WHERE key='dictation'"
            ).fetchone()
            try:
                settings_values = json.loads(settings_row[0]) if settings_row else {}
            except (TypeError, json.JSONDecodeError):
                settings_values = {}
            settings = DictationSettings.from_dict(settings_values)
            history_cutoff = datetime.now(timezone.utc) - timedelta(
                days=settings.history_retention_days
            )
            connection.execute(
                "DELETE FROM dictation_history WHERE created_at < ?",
                (history_cutoff.isoformat(),),
            )
            connection.execute(
                "DELETE FROM speech_improvement_logs WHERE expires_at <= ?",
                (_utc_now(),),
            )

    def get_settings(self) -> DictationSettings:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM settings WHERE key='dictation'"
            ).fetchone()
        try:
            values = json.loads(row[0]) if row else {}
        except (TypeError, json.JSONDecodeError):
            values = {}
        return DictationSettings.from_dict(values)

    def save_settings(self, settings: DictationSettings) -> None:
        payload = json.dumps(settings.to_dict(), sort_keys=True)
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO settings(key, value_json) VALUES('dictation', ?)",
                (payload,),
            )

    def purge_private_data(
        self,
        clear_product_log: Callable[[], int | None] | None = None,
    ) -> PrivateDataPurgeResult:
        """Reset private database state and optionally clear only the product log.

        This is a logical purge plus best-effort SQLite compaction. It is not a
        secure-erase guarantee for SSDs, backups, snapshots, or copied files.
        The optional no-argument callback is invoked at most once; no path is
        accepted or deleted by the storage layer.
        """

        tables = (
            "dictation_history",
            "speech_improvement_logs",
            "review_feedback",
            "learned_corrections",
            "snippets",
            "recovery_items",
            "document_revisions",
            "documents",
            "evaluation_trials",
            "evaluation_runs",
            "settings",
            "vocabulary",
        )
        counts: dict[str, int] = {}
        defaults = DictationSettings().to_dict()
        stock_vocabulary = _default_vocabulary()
        with self._lock, self._connect() as connection:
            for table in tables:
                counts[table] = int(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                )
            counts["schema_meta_private"] = int(
                connection.execute(
                    "SELECT COUNT(*) FROM schema_meta WHERE key != 'schema_version'"
                ).fetchone()[0]
            )

            connection.execute("DROP TRIGGER IF EXISTS document_revisions_immutable_delete")
            for table in (
                "evaluation_trials",
                "evaluation_runs",
                "document_revisions",
                "documents",
                "dictation_history",
                "speech_improvement_logs",
                "review_feedback",
                "learned_corrections",
                "snippets",
                "recovery_items",
                "settings",
                "vocabulary",
            ):
                connection.execute(f"DELETE FROM {table}")
            connection.execute("DELETE FROM schema_meta")
            connection.execute(
                "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            connection.execute(
                "INSERT INTO settings(key, value_json) VALUES('dictation', ?)",
                (json.dumps(defaults, sort_keys=True),),
            )
            now = _utc_now()
            connection.executemany(
                """
                INSERT INTO vocabulary(spoken_form, written_form, category, created_at)
                VALUES(?, ?, ?, ?)
                """,
                [(spoken, written, category, now) for spoken, written, category in stock_vocabulary],
            )
            connection.execute(
                """
                CREATE TRIGGER document_revisions_immutable_delete
                BEFORE DELETE ON document_revisions
                BEGIN
                    SELECT RAISE(ABORT, 'Document revisions are immutable.');
                END
                """
            )
            placeholders = ",".join("?" for _ in tables)
            connection.execute(
                f"DELETE FROM sqlite_sequence WHERE name IN ({placeholders})",
                tables,
            )

        checkpoint_status, vacuum_status = self._compact_after_purge()
        self._database.rotate_key()
        log_status = "not_configured"
        log_files_cleared = 0
        if clear_product_log is not None:
            try:
                callback_result = clear_product_log()
                if callback_result is not None:
                    log_files_cleared = min(4, max(0, int(callback_result)))
                log_status = "completed"
            except Exception:
                log_status = "failed"
                log_files_cleared = 0
        return PrivateDataPurgeResult(
            counts=counts,
            defaults_restored={"settings": 1, "vocabulary": len(stock_vocabulary)},
            database_status="completed",
            wal_checkpoint_status=checkpoint_status,
            vacuum_status=vacuum_status,
            product_log_status=log_status,
            product_log_files_cleared=log_files_cleared,
            active_database_key_rotated=True,
        )

    def _compact_after_purge(self) -> tuple[str, str]:
        checkpoint_status = "completed"
        vacuum_status = "completed"
        with self._lock:
            try:
                with self._connect() as connection:
                    before = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if before and int(before[0]) != 0:
                    checkpoint_status = "busy"
            except (OSError, sqlite3.Error, TypeError, ValueError):
                checkpoint_status = "failed"
            try:
                with self._connect() as connection:
                    connection.execute("VACUUM")
            except (OSError, sqlite3.Error):
                vacuum_status = "failed"
            try:
                with self._connect() as connection:
                    after = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if after and int(after[0]) != 0:
                    checkpoint_status = "busy"
            except (OSError, sqlite3.Error, TypeError, ValueError):
                checkpoint_status = "failed"
        return checkpoint_status, vacuum_status

    def set_meta(self, key: str, value: Any) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES(?, ?)",
                (key, json.dumps(value)),
            )

    def get_meta(self, key: str, default: Any = None) -> Any:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM schema_meta WHERE key=?", (key,)
            ).fetchone()
        if not row:
            return default
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return row[0]

    def list_vocabulary(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, spoken_form, written_form, category, created_at
                FROM vocabulary
                ORDER BY spoken_form COLLATE NOCASE
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def vocabulary_map(self) -> dict[str, str]:
        return {
            row["spoken_form"]: row["written_form"]
            for row in self.list_vocabulary()
        }

    def add_vocabulary(self, spoken_form: str, written_form: str, category: str = "custom") -> int:
        spoken = " ".join((spoken_form or "").strip().split())
        written = (written_form or "").strip()
        category = (category or "custom").strip() or "custom"
        if not spoken or not written:
            raise ValueError("Spoken and written forms are required.")
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO vocabulary(spoken_form, written_form, category, created_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(spoken_form) DO UPDATE SET
                    written_form=excluded.written_form,
                    category=excluded.category
                """,
                (spoken, written, category, _utc_now()),
            )
            row = connection.execute(
                "SELECT id FROM vocabulary WHERE spoken_form=? COLLATE NOCASE", (spoken,)
            ).fetchone()
        return int(row[0] if row else cursor.lastrowid)

    def delete_vocabulary(self, item_id: int) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM vocabulary WHERE id=?", (int(item_id),))

    def add_history(
        self,
        raw_text: str,
        final_text: str,
        mode: str,
        *,
        window_title: str = "",
        latency_ms: int | None = None,
        inserted: bool = False,
    ) -> int:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO dictation_history(
                    raw_text, final_text, mode, window_title, latency_ms, inserted, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    raw_text,
                    final_text,
                    mode,
                    window_title,
                    latency_ms,
                    int(bool(inserted)),
                    _utc_now(),
                ),
            )
            return int(cursor.lastrowid)

    def update_history(self, item_id: int, final_text: str, inserted: bool) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE dictation_history SET final_text=?, inserted=? WHERE id=?",
                (final_text, int(bool(inserted)), int(item_id)),
            )

    def list_history(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, raw_text, final_text, mode, window_title,
                       latency_ms, inserted, created_at
                FROM dictation_history
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def purge_expired_history(self, retention_days: int) -> int:
        days = max(1, min(365, int(retention_days)))
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM dictation_history WHERE created_at < ?",
                (cutoff.isoformat(),),
            )
            return max(0, int(cursor.rowcount))

    def clear_history(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM dictation_history")

    def add_speech_improvement_log(
        self,
        *,
        raw_text: str,
        deterministic_text: str,
        suggested_text: str,
        final_text: str,
        audio_pcm: bytes | bytearray | memoryview,
        sample_rate: int,
        mode: str,
        analysis: dict[str, Any],
        retention_days: int = MAX_SPEECH_LOG_RETENTION_DAYS,
        window_title: str = "",
        latency_ms: int | None = None,
        inserted: bool = False,
        status: str = "complete",
    ) -> int:
        audio = bytes(audio_pcm)
        if not audio or len(audio) > MAX_SPEECH_LOG_AUDIO_BYTES or len(audio) % 2:
            raise ValueError("Speech log audio must be non-empty 16-bit PCM within the size limit.")
        rate = int(sample_rate)
        if not 8000 <= rate <= 192000:
            raise ValueError("Speech log sample rate is outside the supported range.")
        days = max(1, min(MAX_SPEECH_LOG_RETENTION_DAYS, int(retention_days)))
        created = datetime.now(timezone.utc).replace(microsecond=0)
        expires = created + timedelta(days=days)
        duration_ms = int((len(audio) // 2) * 1000 / rate)
        payload = json.dumps(analysis, sort_keys=True, separators=(",", ":"))
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM speech_improvement_logs WHERE expires_at <= ?",
                (created.isoformat(),),
            )
            cursor = connection.execute(
                """
                INSERT INTO speech_improvement_logs(
                    raw_text, deterministic_text, suggested_text, final_text, audio_pcm,
                    sample_rate, duration_ms, mode, window_title, latency_ms, inserted,
                    status, analysis_json, created_at, expires_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    raw_text, deterministic_text, suggested_text, final_text,
                    sqlite3.Binary(audio), rate, duration_ms, mode, window_title,
                    latency_ms, int(bool(inserted)), status, payload,
                    created.isoformat(), expires.isoformat(),
                ),
            )
            return int(cursor.lastrowid)

    def list_speech_improvement_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM speech_improvement_logs WHERE expires_at <= ?", (now,)
            )
            rows = connection.execute(
                """
                SELECT id, raw_text, deterministic_text, suggested_text, final_text,
                       sample_rate, duration_ms, mode, window_title, latency_ms, inserted,
                       status, review_decision, punctuation_wrong, user_notes,
                       analysis_json, created_at, expires_at
                FROM speech_improvement_logs
                WHERE expires_at > ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (now, max(1, int(limit))),
            ).fetchall()
        return [_speech_log_row(row) for row in rows]

    def get_speech_improvement_log(self, item_id: int) -> dict[str, Any] | None:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM speech_improvement_logs WHERE expires_at <= ?", (now,)
            )
            row = connection.execute(
                """
                SELECT id, raw_text, deterministic_text, suggested_text, final_text,
                       audio_pcm, sample_rate, duration_ms, mode, window_title, latency_ms,
                       inserted, status, review_decision, punctuation_wrong, user_notes,
                       analysis_json, created_at, expires_at
                FROM speech_improvement_logs
                WHERE id=? AND expires_at > ?
                """,
                (int(item_id), now),
            ).fetchone()
        return _speech_log_row(row) if row is not None else None

    def update_speech_improvement_log(
        self,
        item_id: int,
        *,
        final_text: str | None = None,
        review_decision: str | None = None,
        status: str | None = None,
        punctuation_wrong: bool | None = None,
        user_notes: str | None = None,
        analysis: dict[str, Any] | None = None,
    ) -> None:
        values: list[Any] = []
        assignments: list[str] = []
        for column, value in (
            ("final_text", final_text),
            ("review_decision", review_decision),
            ("status", status),
            ("user_notes", user_notes),
        ):
            if value is not None:
                assignments.append(f"{column}=?")
                values.append(str(value))
        if punctuation_wrong is not None:
            assignments.append("punctuation_wrong=?")
            values.append(int(bool(punctuation_wrong)))
        if analysis is not None:
            assignments.append("analysis_json=?")
            values.append(json.dumps(analysis, sort_keys=True, separators=(",", ":")))
        if not assignments:
            return
        values.append(int(item_id))
        with self._lock, self._connect() as connection:
            connection.execute(
                f"UPDATE speech_improvement_logs SET {', '.join(assignments)} WHERE id=?",
                values,
            )

    def delete_speech_improvement_log(self, item_id: int) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM speech_improvement_logs WHERE id=?", (int(item_id),))

    def clear_speech_improvement_logs(self) -> int:
        with self._lock, self._connect() as connection:
            count = int(connection.execute("SELECT COUNT(*) FROM speech_improvement_logs").fetchone()[0])
            connection.execute("DELETE FROM speech_improvement_logs")
        return count

    def purge_expired_speech_improvement_logs(self) -> int:
        with self._lock, self._connect() as connection:
            count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM speech_improvement_logs WHERE expires_at <= ?",
                    (_utc_now(),),
                ).fetchone()[0]
            )
            connection.execute(
                "DELETE FROM speech_improvement_logs WHERE expires_at <= ?", (_utc_now(),)
            )
        return count

    def add_review_feedback(
        self,
        decision: str,
        *,
        edited: bool,
        mode: str,
        latency_ms: int | None = None,
    ) -> int:
        decision = (decision or "").strip().lower()
        if decision not in {"accepted", "copied", "inserted", "rejected", "retry"}:
            raise ValueError(f"Unsupported review decision: {decision}")
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO review_feedback(decision, edited, mode, latency_ms, created_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                (decision, int(bool(edited)), mode, latency_ms, _utc_now()),
            )
            return int(cursor.lastrowid)

    def list_review_feedback(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, decision, edited, mode, latency_ms, created_at
                FROM review_feedback
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_learned_correction(
        self,
        source_text: str,
        replacement_text: str,
        *,
        enabled: bool = True,
        origin: str = "review",
        replace_existing: bool = False,
    ) -> int:
        rule = validate_learning_rule(source_text, replacement_text)
        source_key = normalize_learning_key(rule.source)
        origin_value = (origin or "review").strip().casefold()
        if origin_value not in {"review", "manual"}:
            raise ValueError("Unsupported learned-correction origin.")
        now = _utc_now()
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT id, replacement_text FROM learned_corrections WHERE source_key=?",
                (source_key,),
            ).fetchone()
            if existing is not None:
                if existing["replacement_text"] != rule.replacement and not replace_existing:
                    raise ValueError("A different correction already exists for this From phrase.")
                connection.execute(
                    """
                    UPDATE learned_corrections
                    SET source_text=?, replacement_text=?, enabled=?, origin=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        rule.source,
                        rule.replacement,
                        int(bool(enabled)),
                        origin_value,
                        now,
                        int(existing["id"]),
                    ),
                )
                return int(existing["id"])
            count = connection.execute("SELECT COUNT(*) FROM learned_corrections").fetchone()[0]
            if int(count) >= MAX_RULES:
                raise ValueError(f"Manuscript supports up to {MAX_RULES} learned corrections.")
            cursor = connection.execute(
                """
                INSERT INTO learned_corrections(
                    source_text, source_key, replacement_text, enabled, origin, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rule.source,
                    source_key,
                    rule.replacement,
                    int(bool(enabled)),
                    origin_value,
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def list_learned_corrections(
        self,
        *,
        enabled_only: bool = False,
        limit: int = MAX_RULES,
    ) -> list[dict[str, Any]]:
        where = "WHERE enabled=1" if enabled_only else ""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, source_text, replacement_text, enabled, origin, created_at, updated_at
                FROM learned_corrections
                {where}
                ORDER BY source_text COLLATE NOCASE
                LIMIT ?
                """,
                (max(1, min(MAX_RULES, int(limit))),),
            ).fetchall()
        return [dict(row) for row in rows]

    def learned_correction_map(self) -> dict[str, str]:
        return {
            str(row["source_text"]): str(row["replacement_text"])
            for row in self.list_learned_corrections(enabled_only=True)
        }

    def set_learned_correction_enabled(self, item_id: int, enabled: bool) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE learned_corrections SET enabled=?, updated_at=? WHERE id=?",
                (int(bool(enabled)), _utc_now(), int(item_id)),
            )

    def delete_learned_correction(self, item_id: int) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM learned_corrections WHERE id=?", (int(item_id),))

    def clear_learned_corrections(self) -> int:
        with self._lock, self._connect() as connection:
            count = int(connection.execute("SELECT COUNT(*) FROM learned_corrections").fetchone()[0])
            connection.execute("DELETE FROM learned_corrections")
        return count

    def add_snippet(self, spoken_form: str, content: str) -> int:
        spoken = " ".join((spoken_form or "").strip().split())
        content = (content or "").strip()
        if not spoken or not content:
            raise ValueError("Snippet trigger and content are required.")
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO snippets(spoken_form, content, created_at)
                VALUES(?, ?, ?)
                ON CONFLICT(spoken_form) DO UPDATE SET content=excluded.content
                """,
                (spoken, content, _utc_now()),
            )
            row = connection.execute(
                "SELECT id FROM snippets WHERE spoken_form=? COLLATE NOCASE",
                (spoken,),
            ).fetchone()
        return int(row[0] if row else cursor.lastrowid)

    def list_snippets(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, spoken_form, content, created_at
                FROM snippets
                ORDER BY spoken_form COLLATE NOCASE
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def snippet_map(self) -> dict[str, str]:
        return {row["spoken_form"]: row["content"] for row in self.list_snippets()}

    def delete_snippet(self, item_id: int) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM snippets WHERE id=?", (int(item_id),))

    def add_recovery_item(self, final_text: str, mode: str, error_message: str = "") -> int:
        text = (final_text or "").strip()
        if not text:
            raise ValueError("Recovery text is required.")
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO recovery_items(final_text, mode, error_message, created_at)
                VALUES(?, ?, ?, ?)
                """,
                (text, mode, (error_message or "").strip(), _utc_now()),
            )
            return int(cursor.lastrowid)

    def list_recovery_items(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, final_text, mode, error_message, session_key, status, created_at
                FROM recovery_items
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def checkpoint_recovery(
        self,
        session_key: str,
        final_text: str,
        mode: str,
        *,
        status: str = "recording",
        error_message: str = "",
    ) -> int:
        key = (session_key or "").strip()
        text = (final_text or "").strip()
        if not key or not text:
            raise ValueError("Recovery session and text are required.")
        if status not in {"recording", "failed", "insertion_uncertain"}:
            raise ValueError(f"Unsupported recovery status: {status}")
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM recovery_items WHERE session_key=? ORDER BY id DESC LIMIT 1",
                (key,),
            ).fetchone()
            if row:
                item_id = int(row[0])
                connection.execute(
                    """
                    UPDATE recovery_items
                    SET final_text=?, mode=?, error_message=?, status=?
                    WHERE id=?
                    """,
                    (text, mode, (error_message or "").strip(), status, item_id),
                )
                return item_id
            cursor = connection.execute(
                """
                INSERT INTO recovery_items(
                    final_text, mode, error_message, session_key, status, created_at
                ) VALUES(?, ?, ?, ?, ?, ?)
                """,
                (text, mode, (error_message or "").strip(), key, status, _utc_now()),
            )
            return int(cursor.lastrowid)

    def mark_recovery_failed(self, session_key: str, error_message: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE recovery_items SET status='failed', error_message=?
                WHERE session_key=?
                """,
                ((error_message or "").strip(), (session_key or "").strip()),
            )

    def delete_recovery_session(self, session_key: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM recovery_items WHERE session_key=?",
                ((session_key or "").strip(),),
            )

    def delete_recovery_item(self, item_id: int) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM recovery_items WHERE id=?", (int(item_id),))

    def clear_recovery_items(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM recovery_items")

    def create_document(
        self,
        title: str,
        content: str = "",
        *,
        label: str = "Created",
    ) -> int:
        title_value = _document_title(title)
        content_value = _document_content(content)
        label_value = _revision_label(label, fallback="Created")
        now = _utc_now()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO documents(title, content, current_revision, created_at, updated_at)
                VALUES(?, ?, 1, ?, ?)
                """,
                (title_value, content_value, now, now),
            )
            document_id = int(cursor.lastrowid)
            connection.execute(
                """
                INSERT INTO document_revisions(
                    document_id, revision_number, title, content, label,
                    restored_from_revision, created_at
                ) VALUES(?, 1, ?, ?, ?, NULL, ?)
                """,
                (document_id, title_value, content_value, label_value, now),
            )
        return document_id

    def get_document(self, document_id: int) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, title, content, current_revision, created_at, updated_at
                FROM documents WHERE id=?
                """,
                (int(document_id),),
            ).fetchone()
        return dict(row) if row else None

    def list_documents(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, title, content, current_revision, created_at, updated_at
                FROM documents
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_document(
        self,
        document_id: int,
        content: str,
        *,
        title: str | None = None,
        label: str = "Saved",
        restored_from_revision: int | None = None,
        expected_revision: int | None = None,
    ) -> int:
        content_value = _document_content(content)
        label_value = _revision_label(label, fallback="Saved")
        restored_from = (
            int(restored_from_revision) if restored_from_revision is not None else None
        )
        expected = int(expected_revision) if expected_revision is not None else None
        if expected is not None and expected < 1:
            raise ValueError("Expected revision must be positive.")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """
                SELECT title, content, current_revision FROM documents WHERE id=?
                """,
                (int(document_id),),
            ).fetchone()
            if current is None:
                raise LookupError("Document was not found.")
            current_revision = int(current["current_revision"])
            if expected is not None and current_revision != expected:
                raise DocumentRevisionConflictError(
                    f"Document changed from revision {expected} to {current_revision}."
                )
            if restored_from is not None:
                source = connection.execute(
                    """
                    SELECT 1 FROM document_revisions
                    WHERE document_id=? AND revision_number=?
                    """,
                    (int(document_id), restored_from),
                ).fetchone()
                if source is None:
                    raise LookupError("Document revision was not found.")
            title_value = _document_title(title if title is not None else current["title"])
            if (
                restored_from is None
                and title_value == str(current["title"])
                and content_value == str(current["content"])
            ):
                return current_revision
            revision_number = current_revision + 1
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO document_revisions(
                    document_id, revision_number, title, content, label,
                    restored_from_revision, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(document_id),
                    revision_number,
                    title_value,
                    content_value,
                    label_value,
                    restored_from,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE documents
                SET title=?, content=?, current_revision=?, updated_at=?
                WHERE id=?
                """,
                (title_value, content_value, revision_number, now, int(document_id)),
            )
        return revision_number

    def get_document_revision(
        self,
        document_id: int,
        revision_number: int,
    ) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, document_id, revision_number, title, content, label,
                       restored_from_revision, created_at
                FROM document_revisions
                WHERE document_id=? AND revision_number=?
                """,
                (int(document_id), int(revision_number)),
            ).fetchone()
        return dict(row) if row else None

    def list_document_revisions(
        self,
        document_id: int,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, document_id, revision_number, title, content, label,
                       restored_from_revision, created_at
                FROM document_revisions
                WHERE document_id=?
                ORDER BY revision_number DESC
                LIMIT ?
                """,
                (int(document_id), max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in rows]

    def start_evaluation_run(self, kind: str, commit_id: str = "") -> int:
        kind = (kind or "").strip().lower()
        if kind not in {"baseline", "final"}:
            raise ValueError("Evaluation kind must be baseline or final.")
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO evaluation_runs(kind, commit_id, status, started_at)
                VALUES(?, ?, 'active', ?)
                """,
                (kind, (commit_id or "").strip(), _utc_now()),
            )
            return int(cursor.lastrowid)

    def get_active_evaluation_run(self) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, kind, commit_id, status, started_at, completed_at
                FROM evaluation_runs WHERE status='active' ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        return dict(row) if row else None

    def get_evaluation_run(self, run_id: int) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, kind, commit_id, status, started_at, completed_at
                FROM evaluation_runs WHERE id=?
                """,
                (int(run_id),),
            ).fetchone()
        return dict(row) if row else None

    def start_evaluation_trial(self, run_id: int, case_id: str) -> int:
        case = (case_id or "").strip()
        if not case:
            raise ValueError("Evaluation case ID is required.")
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(trial), 0) + 1 FROM evaluation_trials WHERE run_id=? AND case_id=?",
                (int(run_id), case),
            ).fetchone()
            trial = int(row[0] if row else 1)
            cursor = connection.execute(
                """
                INSERT INTO evaluation_trials(run_id, case_id, trial, started_at)
                VALUES(?, ?, ?, ?)
                """,
                (int(run_id), case, trial, _utc_now()),
            )
            return int(cursor.lastrowid)

    def mark_evaluation_output_ready(self, trial_id: int) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE evaluation_trials SET output_ready_at=COALESCE(output_ready_at, ?)
                WHERE id=?
                """,
                (_utc_now(), int(trial_id)),
            )

    def complete_evaluation_trial(
        self,
        trial_id: int,
        decision: str,
        *,
        edited: bool = False,
        final_text: str = "",
        notes: str = "",
    ) -> None:
        decision = (decision or "").strip().lower()
        if decision not in {"accepted", "rejected", "retry"}:
            raise ValueError(f"Unsupported evaluation decision: {decision}")
        now = _utc_now()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT started_at FROM evaluation_trials WHERE id=?",
                (int(trial_id),),
            ).fetchone()
            if not row:
                raise ValueError("Evaluation trial was not found.")
            latency = _elapsed_ms(str(row[0]), now)
            connection.execute(
                """
                UPDATE evaluation_trials
                SET decision=?, edited=?, latency_ms=?, final_text=?, notes=?, accepted_at=?
                WHERE id=?
                """,
                (
                    decision,
                    int(bool(edited)),
                    latency,
                    (final_text or "").strip(),
                    (notes or "").strip(),
                    now,
                    int(trial_id),
                ),
            )

    def list_evaluation_trials(self, run_id: int) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, run_id, case_id, trial, decision, edited, latency_ms,
                       final_text, notes, started_at, output_ready_at, accepted_at
                FROM evaluation_trials WHERE run_id=? ORDER BY id
                """,
                (int(run_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def finish_evaluation_run(self, run_id: int) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE evaluation_runs SET status='complete', completed_at=? WHERE id=?
                """,
                (_utc_now(), int(run_id)),
            )

    def import_legacy_dictation(self, source_path: str | Path | None = None) -> dict[str, int]:
        if self.get_meta("legacy_dictation_imported", False):
            return {"settings": 0, "vocabulary": 0}
        source = validate_local_data_file(
            Path(source_path) if source_path else _legacy_database_path(),
            purpose="Legacy dictation database",
            allowed_suffixes={".db"},
        )
        if not source.exists():
            raise FileNotFoundError(f"Legacy dictation database was not found: {source}")

        imported_settings = 0
        imported_words = 0
        source_connection = plain_sqlite.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
        source_connection.row_factory = plain_sqlite.Row
        try:
            current = self.get_settings()
            keys = {
                "dictation_hotkey": "hotkey",
                "dictation_language": "language",
                "ollama_model": "ollama_model",
            }
            available = {
                row["key"]: row["value"]
                for row in source_connection.execute(
                    "SELECT key, value FROM settings WHERE key IN (?, ?, ?)", tuple(keys)
                ).fetchall()
            }
            for old_key, field in keys.items():
                value = available.get(old_key)
                if value:
                    if field == "language" and "-" in value:
                        value = value.split("-", 1)[0]
                    setattr(current, field, value)
                    imported_settings += 1
            self.save_settings(current)

            rows = source_connection.execute(
                "SELECT spoken_form, written_form, COALESCE(category, 'custom') category FROM dictionary"
            ).fetchall()
            for row in rows:
                self.add_vocabulary(row["spoken_form"], row["written_form"], row["category"])
                imported_words += 1
        finally:
            source_connection.close()
        self.set_meta("legacy_dictation_imported", True)
        return {"settings": imported_settings, "vocabulary": imported_words}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _speech_log_row(row) -> dict[str, Any]:
    result = dict(row)
    try:
        result["analysis"] = json.loads(result.pop("analysis_json"))
    except (TypeError, json.JSONDecodeError):
        result["analysis"] = {
            "version": 1,
            "summary": "The saved analysis could not be read.",
            "counts": {},
            "issues": [],
        }
    if "audio_pcm" in result:
        result["audio_pcm"] = bytes(result["audio_pcm"])
    return result


def _elapsed_ms(started_at: str, ended_at: str) -> int:
    try:
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(ended_at)
        return max(0, int((end - start).total_seconds() * 1000))
    except ValueError:
        return 0


def _document_title(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("Document title must be text.")
    title = " ".join((value or "").strip().split())
    if not title:
        raise ValueError("Document title is required.")
    if len(title) > 240:
        raise ValueError("Document title must be 240 characters or fewer.")
    if "\x00" in title:
        raise ValueError("Document title cannot contain NUL characters.")
    return title


def _document_content(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("Document content must be text.")
    if "\x00" in value:
        raise ValueError("Document content cannot contain NUL characters.")
    return value


def _revision_label(value: str, *, fallback: str) -> str:
    if not isinstance(value, str):
        raise TypeError("Revision label must be text.")
    label = " ".join((value or "").strip().split()) or fallback
    if len(label) > 240:
        raise ValueError("Revision label must be 240 characters or fewer.")
    if "\x00" in label:
        raise ValueError("Revision label cannot contain NUL characters.")
    return label


def _ensure_column(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    declaration: str,
) -> None:
    columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def _legacy_database_path() -> Path:
    import os

    local = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(local) / "LocalDictation" / "dictation.db"


def _default_vocabulary() -> tuple[tuple[str, str, str], ...]:
    return ()


def _legacy_default_vocabulary() -> tuple[tuple[str, str, str], ...]:
    return (
        ("echo link", "Manuscript", "product"),
        ("open claw", "OpenClaw", "technical"),
        ("zorro", "Zoro", "technical"),
        ("faster whisper", "faster-whisper", "technical"),
        ("power shell", "PowerShell", "technical"),
        ("sequel", "SQL", "technical"),
    )
