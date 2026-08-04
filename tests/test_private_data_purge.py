from __future__ import annotations

import json
import os

import echo_link_app.settings_dialog as settings_module
import pytest
from PySide6.QtWidgets import QApplication
from echo_link_app.settings_dialog import SettingsDialog
from echo_link_app.theme import apply_dark_theme
from echo_link_core.models import DictationSettings
from echo_link_core.storage import EchoLinkStore, SCHEMA_VERSION


@pytest.fixture(scope="module")
def app():
    os.environ["ECHOLINK_DISABLE_SOUNDS"] = "1"
    os.environ["ECHOLINK_DISABLE_MEDIA_CONTROL"] = "1"
    application = QApplication.instance() or QApplication([])
    application.setStyle("Fusion")
    apply_dark_theme(application)
    return application


def _fill_private_tables(store: EchoLinkStore) -> dict[str, int]:
    settings = store.get_settings()
    settings.keep_history = True
    settings.personal_learning_enabled = True
    settings.hotkey = "ctrl+f8"
    store.save_settings(settings)
    store.add_vocabulary("private project", "PrivateProject", "custom")
    store.add_history("private raw", "Private final.", "fast", window_title="Secret window")
    store.add_speech_improvement_log(
        raw_text="private raw",
        deterministic_text="Private raw.",
        suggested_text="Private raw.",
        final_text="Private raw.",
        audio_pcm=b"\x01\x00" * 160,
        sample_rate=16000,
        mode="fast",
        analysis={"version": 1, "summary": "Private analysis", "counts": {}, "issues": []},
    )
    store.add_review_feedback("accepted", edited=True, mode="fast", latency_ms=12)
    store.add_learned_correction("private teh", "private the")
    store.add_snippet("private signoff", "Private regards")
    store.add_recovery_item("Private recovery", "fast", "Private error")
    document_id = store.create_document("Private document", "Private content")
    store.update_document(document_id, content="Private revision", expected_revision=1)
    run_id = store.start_evaluation_run("final", "private-commit")
    trial_id = store.start_evaluation_trial(run_id, "private-case")
    store.complete_evaluation_trial(
        trial_id,
        "accepted",
        final_text="Private accepted text",
        notes="Private notes",
    )
    store.set_meta("private_session", {"name": "Private metadata"})
    with store._connect() as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
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
        }


def test_purge_deletes_every_private_table_resets_defaults_and_reports_exact_counts(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    before = _fill_private_tables(store)
    callback_calls = []

    result = store.purge_private_data(
        clear_product_log=lambda: callback_calls.append(True) or 2,
    )

    assert callback_calls == [True]
    assert {name: result.counts[name] for name in before} == before
    assert result.counts["schema_meta_private"] >= 1
    assert result.deleted_row_count == sum(result.counts.values())
    assert result.database_status == "completed"
    assert result.wal_checkpoint_status in {"completed", "busy"}
    assert result.vacuum_status == "completed"
    assert result.product_log_status == "completed"
    assert result.product_log_files_cleared == 2
    assert result.secure_erase_claimed is False

    with store._connect() as connection:
        for table in (
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
        ):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        meta = connection.execute("SELECT key, value FROM schema_meta").fetchall()
        assert [tuple(row) for row in meta] == [("schema_version", str(SCHEMA_VERSION))]
        triggers = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
        }
        assert "document_revisions_immutable_delete" in triggers

    assert store.get_settings() == DictationSettings()
    vocabulary_dump = json.dumps(store.list_vocabulary())
    assert "PrivateProject" not in vocabulary_dump
    assert len(store.list_vocabulary()) == result.defaults_restored["vocabulary"]


def test_purge_without_log_callback_documents_that_log_was_not_configured(tmp_path):
    result = EchoLinkStore(tmp_path / "echo-link.db").purge_private_data()
    assert result.product_log_status == "not_configured"
    assert result.product_log_files_cleared == 0
    assert result.secure_erase_claimed is False


def test_log_callback_failure_does_not_undo_completed_database_purge(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    store.add_history("raw", "final", "fast")

    def fail_log_clear():
        raise PermissionError("private path must not enter result")

    result = store.purge_private_data(clear_product_log=fail_log_clear)
    assert result.database_status == "completed"
    assert result.product_log_status == "failed"
    assert result.product_log_files_cleared == 0
    assert store.list_history() == []


def test_settings_requires_exact_typed_confirmation(app, tmp_path, monkeypatch):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    store.add_history("keep raw", "Keep final", "fast")
    monkeypatch.setattr(SettingsDialog, "_refresh_model_status", lambda _self: None)
    dialog = SettingsDialog(store, engine=None)
    warnings = []
    monkeypatch.setattr(
        settings_module.QInputDialog,
        "getText",
        lambda *_args, **_kwargs: ("DELETE PRIVATE DATA", True),
    )
    monkeypatch.setattr(
        settings_module.QMessageBox,
        "warning",
        lambda *_args: warnings.append(True),
    )
    dialog._delete_all_private_data()
    assert warnings == [True]
    assert len(store.list_history()) == 1
    assert dialog.result() == 0
    dialog.close()


def test_settings_confirmed_purge_closes_with_defaults_and_clear_status(
    app,
    tmp_path,
    monkeypatch,
):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    _fill_private_tables(store)
    monkeypatch.setattr(SettingsDialog, "_refresh_model_status", lambda _self: None)
    dialog = SettingsDialog(store, engine=None)
    messages = []
    monkeypatch.setattr(
        settings_module.QInputDialog,
        "getText",
        lambda *_args, **_kwargs: (SettingsDialog.PURGE_CONFIRMATION, True),
    )
    monkeypatch.setattr(settings_module, "_clear_product_log_files", lambda: 3)
    monkeypatch.setattr(
        settings_module.QMessageBox,
        "information",
        lambda _parent, _title, text: messages.append(text),
    )
    dialog._delete_all_private_data()
    assert dialog.result() == int(settings_module.QDialog.DialogCode.Accepted)
    assert dialog.settings == DictationSettings()
    assert store.list_history() == []
    assert "Product log: completed (3 files cleared)" in messages[0]
    assert "does not claim secure erasure" in messages[0]


def test_log_clear_is_bounded_to_four_fixed_product_files(tmp_path, monkeypatch):
    product_log = tmp_path / "echo-link.log"
    expected = [product_log, *(tmp_path / f"echo-link.log.{index}" for index in range(1, 4))]
    for path in expected:
        path.write_text(f"private {path.name}", encoding="utf-8")
    unrelated = tmp_path / "other.log"
    unrelated.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(settings_module, "log_path", lambda: product_log)
    assert settings_module._clear_product_log_files() == 4
    assert all(path.read_text(encoding="utf-8") == "" for path in expected)
    assert unrelated.read_text(encoding="utf-8") == "keep"
