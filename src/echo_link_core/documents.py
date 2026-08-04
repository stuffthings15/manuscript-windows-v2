from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .data_protection import LocalExportGuard
from .storage import DocumentRevisionConflictError, EchoLinkStore
from .local_paths import is_network_path


@dataclass(frozen=True, slots=True)
class LocalDocument:
    id: int
    title: str
    content: str
    current_revision: int
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class DocumentRevision:
    id: int
    document_id: int
    revision_number: int
    title: str
    content: str
    label: str
    restored_from_revision: int | None
    created_at: str


class DocumentNotFoundError(LookupError):
    pass


class RevisionNotFoundError(LookupError):
    pass


class DocumentConflictError(RuntimeError):
    pass


class DocumentWorkspace:
    """Local SQLite document workspace with append-only revisions."""

    def __init__(self, store: EchoLinkStore):
        if is_network_path(str(store.path)):
            raise ValueError("The document workspace must use a local database.")
        self.store = store

    def create_document(
        self,
        title: str,
        content: str = "",
        *,
        label: str = "Created",
    ) -> LocalDocument:
        document_id = self.store.create_document(title, content, label=label)
        return self.open_document(document_id)

    def open_document(self, document_id: int) -> LocalDocument:
        row = self.store.get_document(document_id)
        if row is None:
            raise DocumentNotFoundError(f"Document {int(document_id)} was not found.")
        return _as_document(row)

    def list_documents(self, *, limit: int = 100) -> tuple[LocalDocument, ...]:
        return tuple(_as_document(row) for row in self.store.list_documents(limit))

    def update_document(
        self,
        document_id: int,
        content: str,
        *,
        title: str | None = None,
        label: str = "Saved",
        expected_revision: int | None = None,
    ) -> LocalDocument:
        try:
            self.store.update_document(
                document_id,
                content,
                title=title,
                label=label,
                expected_revision=expected_revision,
            )
        except DocumentRevisionConflictError as exc:
            raise DocumentConflictError(str(exc)) from exc
        except LookupError as exc:
            raise DocumentNotFoundError(f"Document {int(document_id)} was not found.") from exc
        return self.open_document(document_id)

    def list_revisions(
        self,
        document_id: int,
        *,
        limit: int = 100,
    ) -> tuple[DocumentRevision, ...]:
        self.open_document(document_id)
        return tuple(
            _as_revision(row)
            for row in self.store.list_document_revisions(document_id, limit)
        )

    def read_revision(
        self,
        document_id: int,
        revision_number: int,
    ) -> DocumentRevision:
        row = self.store.get_document_revision(document_id, revision_number)
        if row is None:
            if self.store.get_document(document_id) is None:
                raise DocumentNotFoundError(f"Document {int(document_id)} was not found.")
            raise RevisionNotFoundError(
                f"Revision {int(revision_number)} was not found for document {int(document_id)}."
            )
        return _as_revision(row)

    def restore_revision(
        self,
        document_id: int,
        revision_number: int,
        *,
        label: str | None = None,
        expected_revision: int | None = None,
    ) -> LocalDocument:
        source = self.read_revision(document_id, revision_number)
        current = self.open_document(document_id)
        expected = current.current_revision if expected_revision is None else int(expected_revision)
        try:
            self.store.update_document(
                document_id,
                source.content,
                title=source.title,
                label=label or f"Restored revision {source.revision_number}",
                restored_from_revision=source.revision_number,
                expected_revision=expected,
            )
        except DocumentRevisionConflictError as exc:
            raise DocumentConflictError(str(exc)) from exc
        except LookupError as exc:
            raise DocumentNotFoundError(f"Document {int(document_id)} was not found.") from exc
        return self.open_document(document_id)

    def export_document(
        self,
        document_id: int,
        destination_path: str | Path,
        *,
        overwrite: bool = False,
    ) -> Path:
        """Write one document only to the exact local path supplied by the caller."""

        if destination_path is None or not str(destination_path).strip():
            raise ValueError("Choose an export path.")
        destination = Path(destination_path).expanduser().absolute()
        if destination == self.store.path.resolve(strict=False):
            raise ValueError("The document database cannot be an export target.")

        document = self.open_document(document_id)
        receipt = LocalExportGuard().write_text(
            destination,
            "document",
            document.content,
            contains_user_text=True,
            allowed_suffixes={".txt", ".md"},
            overwrite=overwrite,
            preserve_user_text=True,
        )
        return receipt.path


def _as_document(row: dict[str, Any]) -> LocalDocument:
    return LocalDocument(
        id=int(row["id"]),
        title=str(row["title"]),
        content=str(row["content"]),
        current_revision=int(row["current_revision"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _as_revision(row: dict[str, Any]) -> DocumentRevision:
    restored = row.get("restored_from_revision")
    return DocumentRevision(
        id=int(row["id"]),
        document_id=int(row["document_id"]),
        revision_number=int(row["revision_number"]),
        title=str(row["title"]),
        content=str(row["content"]),
        label=str(row["label"]),
        restored_from_revision=int(restored) if restored is not None else None,
        created_at=str(row["created_at"]),
    )


__all__ = [
    "DocumentConflictError",
    "DocumentNotFoundError",
    "DocumentRevision",
    "DocumentWorkspace",
    "LocalDocument",
    "RevisionNotFoundError",
]
