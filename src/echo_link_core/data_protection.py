from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from .local_paths import is_network_path


PRODUCT_ID = "echo-link"
EXPORT_SCHEMA_VERSION = 1
MAX_EXPORT_ITEMS = 100_000
MAX_EXPORT_DEPTH = 32
MAX_EXPORT_BYTES = 25 * 1024 * 1024
REDACTED = "[REDACTED]"

# This is an allowlisted inventory of every SQLite TEXT column. It is kept in
# code so a schema change fails the privacy audit until its retention class is
# reviewed. There are deliberately no product secret fields in schema v8.
PERSISTED_TEXT_FIELD_POLICY: dict[str, dict[str, str]] = {
    "schema_meta": {
        "key": "configuration",
        "value": "configuration",
    },
    "settings": {
        "key": "configuration",
        "value_json": "configuration",
    },
    "vocabulary": {
        "spoken_form": "user_text",
        "written_form": "user_text",
        "category": "metadata",
        "created_at": "metadata",
    },
    "dictation_history": {
        "raw_text": "sensitive_user_text",
        "final_text": "sensitive_user_text",
        "mode": "metadata",
        "window_title": "sensitive_context",
        "created_at": "metadata",
    },
    "speech_improvement_logs": {
        "raw_text": "sensitive_user_text",
        "deterministic_text": "sensitive_user_text",
        "suggested_text": "sensitive_user_text",
        "final_text": "sensitive_user_text",
        "mode": "metadata",
        "window_title": "sensitive_context",
        "status": "metadata",
        "review_decision": "metadata",
        "user_notes": "sensitive_user_text",
        "analysis_json": "sensitive_context",
        "created_at": "metadata",
        "expires_at": "metadata",
    },
    "review_feedback": {
        "decision": "metadata",
        "mode": "metadata",
        "created_at": "metadata",
    },
    "learned_corrections": {
        "source_text": "user_text",
        "source_key": "user_text",
        "replacement_text": "user_text",
        "origin": "metadata",
        "created_at": "metadata",
        "updated_at": "metadata",
    },
    "snippets": {
        "spoken_form": "sensitive_user_text",
        "content": "sensitive_user_text",
        "created_at": "metadata",
    },
    "recovery_items": {
        "final_text": "sensitive_user_text",
        "mode": "metadata",
        "error_message": "sensitive_context",
        "session_key": "internal_identifier",
        "status": "metadata",
        "created_at": "metadata",
    },
    "documents": {
        "title": "sensitive_user_text",
        "content": "sensitive_user_text",
        "created_at": "metadata",
        "updated_at": "metadata",
    },
    "document_revisions": {
        "title": "sensitive_user_text",
        "content": "sensitive_user_text",
        "label": "sensitive_user_text",
        "created_at": "metadata",
    },
    "evaluation_runs": {
        "kind": "metadata",
        "commit_id": "metadata",
        "status": "metadata",
        "started_at": "metadata",
        "completed_at": "metadata",
    },
    "evaluation_trials": {
        "case_id": "metadata",
        "decision": "metadata",
        "final_text": "sensitive_user_text",
        "notes": "sensitive_user_text",
        "started_at": "metadata",
        "output_ready_at": "metadata",
        "accepted_at": "metadata",
    },
}

PERSISTED_SECRET_FIELDS: frozenset[tuple[str, str]] = frozenset()
APPROVED_BLOB_FIELDS: frozenset[tuple[str, str]] = frozenset(
    {("speech_improvement_logs", "audio_pcm")}
)

SECRET_FIELD_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "access_token",
        "refresh_token",
        "token",
        "password",
        "secret",
        "client_secret",
        "private_key",
        "credential",
        "credentials",
    }
)
RAW_AUDIO_FIELD_NAMES = frozenset(
    {
        "audio",
        "raw_audio",
        "audio_blob",
        "audio_bytes",
        "audio_path",
        "audio_pcm",
        "recording",
        "recording_blob",
        "recording_bytes",
        "recording_path",
    }
)
_EXPORT_TYPE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(
        r"(?i)\b(api[ _-]?key|access[ _-]?token|refresh[ _-]?token|password|"
        r"client[ _-]?secret|private[ _-]?key|credential)\b\s*[:=]\s*[^\s,;]+"
    ),
    re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
)


class DataProtectionError(ValueError):
    pass


class UnsafeExportError(DataProtectionError):
    pass


class UnsafePathError(DataProtectionError):
    pass


@dataclass(frozen=True, slots=True)
class ExportManifest:
    schema_version: int
    product_id: str
    export_type: str
    generated_at_utc: str
    included_sections: tuple[str, ...]
    contains_user_text: bool
    contains_raw_audio: bool
    contains_persisted_secrets: bool
    contains_potential_secrets: bool
    redaction_applied: bool
    redacted_fields: tuple[str, ...]
    payload_sha256: str
    payload_bytes: int

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["included_sections"] = list(self.included_sections)
        payload["redacted_fields"] = list(self.redacted_fields)
        return payload


@dataclass(frozen=True, slots=True)
class PreparedExport:
    data: dict[str, Any]
    manifest: ExportManifest

    def bundle(self) -> dict[str, Any]:
        return {**self.data, "export_manifest": self.manifest.to_dict()}


@dataclass(frozen=True, slots=True)
class ExportReceipt:
    path: Path
    manifest: ExportManifest


class LocalExportGuard:
    """Redact structured secrets and write only to an explicit local path."""

    def prepare_json(
        self,
        export_type: str,
        payload: Mapping[str, Any],
        *,
        contains_user_text: bool,
    ) -> PreparedExport:
        kind = _validated_export_type(export_type)
        if "export_manifest" in payload:
            raise UnsafeExportError("Payload cannot provide its own export manifest.")
        sanitized, redacted_fields = sanitize_export_payload(payload)
        canonical = _json_bytes(sanitized, pretty=False)
        manifest = _manifest(
            kind,
            included_sections=tuple(sorted(sanitized)),
            contains_user_text=contains_user_text,
            redacted_fields=redacted_fields,
            payload=canonical,
            contains_potential_secrets=False,
        )
        return PreparedExport(sanitized, manifest)

    def write_json(
        self,
        destination: str | Path,
        export_type: str,
        payload: Mapping[str, Any],
        *,
        contains_user_text: bool,
        overwrite: bool = False,
        create_parent: bool = False,
    ) -> ExportReceipt:
        prepared = self.prepare_json(
            export_type,
            payload,
            contains_user_text=contains_user_text,
        )
        rendered = _json_bytes(prepared.bundle(), pretty=True)
        path = validate_local_export_destination(
            destination,
            allowed_suffixes={".json"},
            overwrite=overwrite,
            create_parent=create_parent,
        )
        _atomic_write(path, rendered, overwrite=overwrite)
        return ExportReceipt(path, prepared.manifest)

    def write_text(
        self,
        destination: str | Path,
        export_type: str,
        text: str,
        *,
        contains_user_text: bool,
        allowed_suffixes: set[str] | frozenset[str],
        overwrite: bool = False,
        create_parent: bool = False,
        preserve_user_text: bool = False,
    ) -> ExportReceipt:
        kind = _validated_export_type(export_type)
        if not isinstance(text, str):
            raise TypeError("Export text must be a string.")
        if "\x00" in text:
            raise UnsafeExportError("Export text cannot contain NUL characters.")
        redacted, changed = redact_secret_text(text)
        output = text if preserve_user_text else redacted
        encoded = output.encode("utf-8")
        if len(encoded) > MAX_EXPORT_BYTES:
            raise UnsafeExportError(f"Export exceeds the {MAX_EXPORT_BYTES} byte limit.")
        manifest = _manifest(
            kind,
            included_sections=("text",),
            contains_user_text=contains_user_text,
            redacted_fields=("/text",) if changed and not preserve_user_text else (),
            payload=encoded,
            contains_potential_secrets=bool(changed and preserve_user_text),
        )
        path = validate_local_export_destination(
            destination,
            allowed_suffixes=allowed_suffixes,
            overwrite=overwrite,
            create_parent=create_parent,
        )
        _atomic_write(path, encoded, overwrite=overwrite)
        return ExportReceipt(path, manifest)


def sanitize_export_payload(payload: Any) -> tuple[Any, tuple[str, ...]]:
    redacted: list[str] = []
    item_count = 0

    def visit(value: Any, path: str, depth: int) -> Any:
        nonlocal item_count
        if depth > MAX_EXPORT_DEPTH:
            raise UnsafeExportError(f"Export nesting exceeds {MAX_EXPORT_DEPTH} levels.")
        item_count += 1
        if item_count > MAX_EXPORT_ITEMS:
            raise UnsafeExportError(f"Export contains more than {MAX_EXPORT_ITEMS} values.")
        if value is None or isinstance(value, (bool, int)):
            return value
        if isinstance(value, float):
            if not math.isfinite(value):
                raise UnsafeExportError("Export cannot contain NaN or infinite values.")
            return value
        if isinstance(value, str):
            cleaned, changed = redact_secret_text(value)
            if changed:
                redacted.append(path or "/")
            return cleaned
        if isinstance(value, (bytes, bytearray, memoryview)):
            raise UnsafeExportError("Binary or raw-audio values cannot be exported.")
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for key, child in value.items():
                if not isinstance(key, str) or not key:
                    raise UnsafeExportError("Export object keys must be non-empty strings.")
                normalized = _normalized_field_name(key)
                child_path = f"{path}/{_json_pointer_part(key)}" if path else f"/{_json_pointer_part(key)}"
                if normalized in RAW_AUDIO_FIELD_NAMES:
                    raise UnsafeExportError(f"Raw audio field is not exportable: {child_path}")
                if normalized in SECRET_FIELD_NAMES:
                    result[key] = REDACTED
                    redacted.append(child_path)
                else:
                    result[key] = visit(child, child_path, depth + 1)
            return result
        if isinstance(value, Sequence):
            return [
                visit(child, f"{path}/{index}" if path else f"/{index}", depth + 1)
                for index, child in enumerate(value)
            ]
        raise UnsafeExportError(f"Unsupported export value type: {type(value).__name__}")

    return visit(payload, "", 0), tuple(dict.fromkeys(redacted))


def redact_secret_text(value: str) -> tuple[str, bool]:
    result = value
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(REDACTED, result)
    return result, result != value


def validate_local_export_destination(
    value: str | Path,
    *,
    allowed_suffixes: set[str] | frozenset[str],
    overwrite: bool,
    create_parent: bool,
) -> Path:
    path = validate_local_data_file(
        value,
        purpose="Export",
        allowed_suffixes=allowed_suffixes,
        create_parent=create_parent,
    )
    if path.exists() and path.is_dir():
        raise IsADirectoryError(str(path))
    if path.exists() and not overwrite:
        raise FileExistsError(str(path))
    return path


def validate_local_data_file(
    value: str | Path,
    *,
    purpose: str,
    allowed_suffixes: set[str] | frozenset[str] | None = None,
    create_parent: bool = False,
) -> Path:
    if value is None or not str(value).strip():
        raise UnsafePathError(f"{purpose} path is required.")
    candidate = Path(value).expanduser()
    if ".." in candidate.parts:
        raise UnsafePathError(f"{purpose} path cannot contain parent traversal.")
    candidate = candidate.absolute()
    if is_network_path(candidate):
        raise UnsafePathError(f"Network {purpose.lower()} paths are not allowed.")
    _reject_linked_components(candidate)
    parent = validate_local_directory(
        candidate.parent,
        purpose=f"{purpose} folder",
        create=create_parent,
    )
    if _is_link(candidate):
        raise UnsafePathError(f"{purpose} path cannot be a link or junction.")
    resolved = candidate.resolve(strict=False)
    if resolved.parent != parent:
        raise UnsafePathError(f"{purpose} path changed while it was being checked.")
    if is_network_path(resolved):
        raise UnsafePathError(f"Network {purpose.lower()} paths are not allowed.")
    if allowed_suffixes:
        suffixes = {item.casefold() for item in allowed_suffixes}
        if resolved.suffix.casefold() not in suffixes:
            allowed = ", ".join(sorted(suffixes))
            raise UnsafePathError(f"{purpose} must use one of: {allowed}.")
    return resolved


def validate_local_directory(
    value: str | Path,
    *,
    purpose: str,
    create: bool,
) -> Path:
    if value is None or not str(value).strip():
        raise UnsafePathError(f"{purpose} is required.")
    candidate = Path(value).expanduser()
    if ".." in candidate.parts:
        raise UnsafePathError(f"{purpose} cannot contain parent traversal.")
    candidate = candidate.absolute()
    if candidate == Path(candidate.anchor):
        raise UnsafePathError(f"{purpose} cannot be a filesystem root.")
    if is_network_path(candidate):
        raise UnsafePathError(f"Network {purpose.lower()} paths are not allowed.")
    _reject_linked_components(candidate)
    if create:
        candidate.mkdir(parents=True, exist_ok=True)
    if not candidate.exists() or not candidate.is_dir():
        raise FileNotFoundError(f"{purpose} was not found: {candidate}")
    _reject_linked_components(candidate)
    resolved = candidate.resolve(strict=True)
    if is_network_path(resolved):
        raise UnsafePathError(f"Network {purpose.lower()} paths are not allowed.")
    return resolved


def audit_persisted_text_schema(connection: sqlite3.Connection) -> list[str]:
    failures: list[str] = []
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    table_info: dict[str, list[sqlite3.Row | tuple[Any, ...]]] = {}
    actual: dict[str, set[str]] = {}
    for table in tables:
        rows = list(connection.execute(f"PRAGMA table_info({table})"))
        table_info[table] = rows
        actual[table] = {
            str(row[1])
            for row in rows
            if "TEXT" in str(row[2]).upper()
        }
    expected = {
        table: set(columns)
        for table, columns in PERSISTED_TEXT_FIELD_POLICY.items()
    }
    for table in sorted(set(actual) | set(expected)):
        if table not in expected:
            failures.append(f"Unreviewed persisted table: {table}")
        unknown = actual.get(table, set()) - expected.get(table, set())
        missing = expected.get(table, set()) - actual.get(table, set())
        if unknown:
            failures.append(f"Unreviewed TEXT fields in {table}: {sorted(unknown)}")
        if missing:
            failures.append(f"Missing reviewed TEXT fields in {table}: {sorted(missing)}")
    for table, rows in table_info.items():
        for row in rows:
            column = str(row[1])
            declared_type = str(row[2]).upper()
            normalized = _normalized_field_name(column)
            if normalized in SECRET_FIELD_NAMES and (table, column) not in PERSISTED_SECRET_FIELDS:
                failures.append(f"Unprotected secret field in {table}.{column}")
            approved_blob = (table, column) in APPROVED_BLOB_FIELDS
            if normalized in RAW_AUDIO_FIELD_NAMES and not approved_blob:
                failures.append(f"Raw-audio field in {table}.{column}")
            if "BLOB" in declared_type and not approved_blob:
                failures.append(f"Unreviewed BLOB field in {table}.{column}")
    return failures


def _manifest(
    export_type: str,
    *,
    included_sections: tuple[str, ...],
    contains_user_text: bool,
    redacted_fields: tuple[str, ...],
    payload: bytes,
    contains_potential_secrets: bool,
) -> ExportManifest:
    return ExportManifest(
        schema_version=EXPORT_SCHEMA_VERSION,
        product_id=PRODUCT_ID,
        export_type=export_type,
        generated_at_utc=datetime.now(UTC).isoformat(),
        included_sections=included_sections,
        contains_user_text=bool(contains_user_text),
        contains_raw_audio=False,
        contains_persisted_secrets=False,
        contains_potential_secrets=bool(contains_potential_secrets),
        redaction_applied=bool(redacted_fields),
        redacted_fields=redacted_fields,
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        payload_bytes=len(payload),
    )


def _validated_export_type(value: str) -> str:
    kind = (value or "").strip().casefold()
    if not _EXPORT_TYPE.fullmatch(kind):
        raise UnsafeExportError("Export type must be a short lowercase identifier.")
    return kind


def _json_bytes(payload: Any, *, pretty: bool) -> bytes:
    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
        allow_nan=False,
    )
    encoded = (rendered + ("\n" if pretty else "")).encode("utf-8")
    if len(encoded) > MAX_EXPORT_BYTES:
        raise UnsafeExportError(f"Export exceeds the {MAX_EXPORT_BYTES} byte limit.")
    return encoded


def _atomic_write(path: Path, payload: bytes, *, overwrite: bool) -> None:
    handle, name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _reject_linked_components(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if current.exists() and _is_link(current):
            raise UnsafePathError("Symbolic-link and junction paths are not allowed.")


def _is_link(path: Path) -> bool:
    try:
        checker = getattr(path, "is_junction", None)
        return path.is_symlink() or bool(checker and checker())
    except OSError:
        return True


def _normalized_field_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _json_pointer_part(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


__all__ = [
    "DataProtectionError",
    "ExportManifest",
    "ExportReceipt",
    "LocalExportGuard",
    "PERSISTED_SECRET_FIELDS",
    "PERSISTED_TEXT_FIELD_POLICY",
    "PreparedExport",
    "UnsafeExportError",
    "UnsafePathError",
    "audit_persisted_text_schema",
    "redact_secret_text",
    "sanitize_export_payload",
    "validate_local_data_file",
    "validate_local_directory",
    "validate_local_export_destination",
]
