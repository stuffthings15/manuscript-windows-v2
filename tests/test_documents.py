from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlcipher3 import dbapi2 as encrypted_sqlite

from echo_link_core.documents import (
    DocumentConflictError,
    DocumentNotFoundError,
    DocumentWorkspace,
    RevisionNotFoundError,
)
from echo_link_core import data_protection as data_protection_module
from echo_link_core.storage import EchoLinkStore


def _workspace(tmp_path) -> DocumentWorkspace:
    return DocumentWorkspace(EchoLinkStore(tmp_path / "echo-link.db"))


def test_create_open_and_list_document_with_initial_revision(tmp_path):
    workspace = _workspace(tmp_path)
    content = "First line.\nSecond line.\n"

    created = workspace.create_document(
        "  Project   Notes  ",
        content,
        label="Initial draft",
    )

    assert created.title == "Project Notes"
    assert created.content == content
    assert created.current_revision == 1
    assert workspace.open_document(created.id) == created
    assert workspace.list_documents() == (created,)
    revisions = workspace.list_revisions(created.id)
    assert len(revisions) == 1
    assert revisions[0].revision_number == 1
    assert revisions[0].content == content
    assert revisions[0].label == "Initial draft"
    created_at = datetime.fromisoformat(revisions[0].created_at)
    assert created_at.utcoffset() == timezone.utc.utcoffset(None)


def test_update_appends_immutable_revision_and_preserves_original(tmp_path):
    workspace = _workspace(tmp_path)
    created = workspace.create_document("Draft", "Version one")

    updated = workspace.update_document(
        created.id,
        "Version two\n",
        title="Final Draft",
        label="Added ending",
    )

    assert updated.title == "Final Draft"
    assert updated.content == "Version two\n"
    assert updated.current_revision == 2
    original = workspace.read_revision(created.id, 1)
    latest = workspace.read_revision(created.id, 2)
    assert original.title == "Draft"
    assert original.content == "Version one"
    assert latest.title == "Final Draft"
    assert latest.label == "Added ending"
    assert [item.revision_number for item in workspace.list_revisions(created.id)] == [2, 1]

    with workspace.store._connect() as connection:
        with pytest.raises(encrypted_sqlite.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE document_revisions SET content='changed' WHERE document_id=?",
                (created.id,),
            )
    with workspace.store._connect() as connection:
        with pytest.raises(encrypted_sqlite.IntegrityError, match="immutable"):
            connection.execute(
                "DELETE FROM document_revisions WHERE document_id=?",
                (created.id,),
            )

    assert workspace.read_revision(created.id, 1) == original
    assert workspace.read_revision(created.id, 2) == latest


def test_restore_creates_new_revision_without_removing_history(tmp_path):
    workspace = _workspace(tmp_path)
    document = workspace.create_document("Notes", "Original")
    workspace.update_document(document.id, "Changed", label="Second draft")

    restored = workspace.restore_revision(document.id, 1)

    assert restored.content == "Original"
    assert restored.current_revision == 3
    revisions = workspace.list_revisions(document.id)
    assert [item.revision_number for item in revisions] == [3, 2, 1]
    assert revisions[0].label == "Restored revision 1"
    assert revisions[0].restored_from_revision == 1
    assert workspace.read_revision(document.id, 2).content == "Changed"


def test_stale_update_is_rejected_and_identical_save_adds_no_revision(tmp_path):
    workspace = _workspace(tmp_path)
    document = workspace.create_document("Notes", "Original")

    unchanged = workspace.update_document(
        document.id,
        "Original",
        expected_revision=1,
    )
    assert unchanged.current_revision == 1
    assert len(workspace.list_revisions(document.id)) == 1

    current = workspace.update_document(
        document.id,
        "New version",
        expected_revision=1,
    )
    assert current.current_revision == 2
    with pytest.raises(DocumentConflictError, match="revision 1 to 2"):
        workspace.update_document(
            document.id,
            "Stale overwrite",
            expected_revision=1,
        )
    with pytest.raises(DocumentConflictError, match="revision 1 to 2"):
        workspace.restore_revision(
            document.id,
            1,
            expected_revision=1,
        )

    assert workspace.open_document(document.id).content == "New version"
    assert len(workspace.list_revisions(document.id)) == 2


def test_unknown_documents_and_revisions_raise_clear_errors(tmp_path):
    workspace = _workspace(tmp_path)
    document = workspace.create_document("Notes")

    with pytest.raises(DocumentNotFoundError):
        workspace.open_document(999)
    with pytest.raises(DocumentNotFoundError):
        workspace.update_document(999, "Missing")
    with pytest.raises(DocumentNotFoundError):
        workspace.list_revisions(999)
    with pytest.raises(RevisionNotFoundError):
        workspace.read_revision(document.id, 999)
    with pytest.raises(RevisionNotFoundError):
        workspace.restore_revision(document.id, 999)


def test_export_requires_exact_local_caller_path_and_explicit_overwrite(tmp_path):
    workspace = _workspace(tmp_path)
    document = workspace.create_document("Notes", "Exact text\n")
    destination = tmp_path / "exported.md"

    exported = workspace.export_document(document.id, destination)

    assert exported == destination.resolve()
    assert destination.read_bytes() == b"Exact text\n"
    with pytest.raises(FileExistsError):
        workspace.export_document(document.id, destination)
    workspace.update_document(document.id, "New text")
    workspace.export_document(document.id, destination, overwrite=True)
    assert destination.read_text(encoding="utf-8") == "New text"
    with pytest.raises(TypeError):
        workspace.export_document(document.id)  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="export path"):
        workspace.export_document(document.id, None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Network"):
        workspace.export_document(document.id, r"\\server\share\notes.txt")
    with pytest.raises(ValueError, match="database"):
        workspace.export_document(document.id, workspace.store.path, overwrite=True)


def test_failed_atomic_overwrite_leaves_old_file_and_no_temporary_file(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    document = workspace.create_document("Notes", "Replacement")
    destination = tmp_path / "notes.txt"
    destination.write_text("Keep this", encoding="utf-8")

    def fail_replace(source: Path, target: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(data_protection_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated"):
        workspace.export_document(document.id, destination, overwrite=True)

    assert destination.read_text(encoding="utf-8") == "Keep this"
    assert list(tmp_path.glob(".notes.txt.*.tmp")) == []


def test_export_rejects_symbolic_link_destination_and_parent(tmp_path):
    workspace = _workspace(tmp_path)
    document = workspace.create_document("Notes", "Local")
    real_file = tmp_path / "real.txt"
    real_file.write_text("Existing", encoding="utf-8")
    linked_file = tmp_path / "linked.txt"
    real_folder = tmp_path / "real-folder"
    real_folder.mkdir()
    linked_folder = tmp_path / "linked-folder"
    try:
        linked_file.symlink_to(real_file)
        linked_folder.symlink_to(real_folder, target_is_directory=True)
    except OSError:
        pytest.skip("Symbolic links are not available on this machine.")

    with pytest.raises(ValueError, match="Symbolic-link"):
        workspace.export_document(document.id, linked_file, overwrite=True)
    with pytest.raises(ValueError, match="Symbolic-link"):
        workspace.export_document(document.id, linked_folder / "notes.txt")


def test_document_schema_stores_text_and_revisions_but_no_audio_or_export_path(tmp_path):
    workspace = _workspace(tmp_path)
    workspace.create_document("Private", "Local content")

    with workspace.store._connect() as connection:
        document_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(documents)").fetchall()
        }
        revision_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(document_revisions)").fetchall()
        }

    all_columns = document_columns | revision_columns
    assert {"title", "content", "current_revision"} <= document_columns
    assert {"revision_number", "label", "restored_from_revision"} <= revision_columns
    assert not {"audio", "raw_audio", "export_path", "network_path"} & all_columns


def test_schema_v6_upgrade_preserves_existing_history_and_adds_workspace(tmp_path):
    database = tmp_path / "echo-link.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta(key, value) VALUES('schema_version', '6');
            CREATE TABLE dictation_history(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                raw_text TEXT NOT NULL,
                final_text TEXT NOT NULL,
                mode TEXT NOT NULL,
                window_title TEXT NOT NULL DEFAULT '',
                latency_ms INTEGER,
                inserted INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO dictation_history(
                raw_text, final_text, mode, window_title, latency_ms, inserted, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "old raw",
                "Old final.",
                "fast",
                "Editor",
                20,
                1,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        connection.commit()

    store = EchoLinkStore(database)
    workspace = DocumentWorkspace(store)

    assert store.get_meta("schema_version") == 8
    assert store.list_history()[0]["final_text"] == "Old final."
    assert workspace.create_document("New workspace", "Text").current_revision == 1
