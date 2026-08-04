from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from echo_link_core.models import DictationMode, DictationSettings, InsertionMode
from echo_link_core.storage import EchoLinkStore


def test_settings_migrate_older_writing_models_to_gemma4():
    for old_model in ("qwen2.5:14b", "qwen3:8b", "qwen3:14b"):
        settings = DictationSettings.from_dict({"ollama_model": old_model})
        assert settings.ollama_model == "gemma4:12b"


def test_settings_migrate_older_polish_packs_to_gemma4():
    for old_pack in (
        "qwen3-1.7b-q4-k-m-v1",
        "qwen3-4b-q4-k-m-v1",
        "qwen3-8b-q4-k-m-v1",
        "qwen3-14b-q4-k-m-v1",
    ):
        settings = DictationSettings.from_dict({"polish_model_pack_id": old_pack})
        assert settings.polish_model_pack_id == "gemma4-12b-q4-k-m-v1"

def test_settings_vocabulary_and_history_round_trip(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    settings = store.get_settings()
    assert settings.mode is DictationMode.FAST
    assert settings.insertion_mode is InsertionMode.DIRECT
    assert settings.device == "cpu"
    assert settings.keep_history is True
    assert settings.history_retention_days == 3
    assert settings.speech_logging_enabled is False
    assert settings.speech_log_retention_days == 15
    assert settings.personal_learning_enabled is False
    assert settings.show_echo_bar is True
    assert settings.sound_enabled is True
    assert settings.close_to_tray is True
    assert settings.pre_roll_ms == 500
    assert settings.max_recording_seconds == 120
    assert settings.start_sound == "Speech On.wav"
    assert settings.stop_sound == "Built-in completion tone"
    assert settings.audio_output_index is None
    assert settings.bluetooth_delay_ms == -1
    assert settings.hands_free_enabled is True
    assert settings.hands_free_hotkey == "ctrl+alt+space"
    assert settings.private_paste_hotkey == "ctrl+alt+v"
    assert settings.silence_auto_stop_enabled is True
    assert settings.silence_timeout_seconds == 2.5
    assert store.get_meta("first_run_setup_pending") is True
    assert store.list_vocabulary() == []

    settings.keep_history = True
    settings.insertion_mode = InsertionMode.DIRECT
    store.save_settings(settings)
    store.add_vocabulary("my project", "MyProject", "name")
    store.add_history("my project", "MyProject.", "fast", latency_ms=123, inserted=True)

    assert store.get_settings().keep_history is True
    assert store.get_settings().insertion_mode is InsertionMode.DIRECT
    assert store.vocabulary_map()["my project"] == "MyProject"
    assert store.list_history()[0]["latency_ms"] == 123


def test_dictation_history_retention_removes_only_expired_rows(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    store.add_history("recent", "Recent.", "fast")
    store.add_history("old", "Old.", "fast")
    with store._connect() as connection:
        connection.execute(
            "UPDATE dictation_history SET created_at=? WHERE raw_text='old'",
            ("2020-01-01T00:00:00+00:00",),
        )

    assert store.purge_expired_history(3) == 1
    assert [row["raw_text"] for row in store.list_history()] == ["recent"]


def test_developer_mode_round_trips_through_local_settings(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    settings = store.get_settings()
    settings.mode = DictationMode.DEVELOPER
    store.save_settings(settings)
    assert store.get_settings().mode is DictationMode.DEVELOPER


def test_review_feedback_keeps_metrics_without_dictation_text(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    store.add_review_feedback(
        "accepted",
        edited=False,
        mode="fast",
        latency_ms=456,
    )
    row = store.list_review_feedback()[0]
    assert row["decision"] == "accepted"
    assert row["edited"] == 0
    assert row["latency_ms"] == 456
    assert "raw_text" not in row
    assert "final_text" not in row


def test_learned_correction_crud_is_local_and_scoped(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    item_id = store.add_learned_correction("teh", "the")
    assert store.learned_correction_map() == {"teh": "the"}
    store.set_learned_correction_enabled(item_id, False)
    assert store.learned_correction_map() == {}
    row = store.list_learned_corrections()[0]
    assert row["source_text"] == "teh"
    assert row["replacement_text"] == "the"
    assert "raw_text" not in row
    assert "final_text" not in row
    store.set_learned_correction_enabled(item_id, True)
    assert store.clear_learned_corrections() == 1
    assert store.list_learned_corrections() == []


def test_duplicate_learned_source_requires_explicit_replacement(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    item_id = store.add_learned_correction("adress", "address")
    with pytest.raises(ValueError):
        store.add_learned_correction("ADRESS", "addressee")
    assert store.add_learned_correction(
        "ADRESS", "addressee", replace_existing=True
    ) == item_id
    assert store.learned_correction_map() == {"ADRESS": "addressee"}


def test_snippets_round_trip_locally(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    item_id = store.add_snippet("email signature", "Regards,\nCurtis")
    assert store.snippet_map()["email signature"] == "Regards,\nCurtis"
    store.delete_snippet(item_id)
    assert store.list_snippets() == []


def test_recovery_items_store_only_final_text_and_can_be_cleared(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    assert store.get_settings().keep_recovery_text is False
    item_id = store.add_recovery_item("Usable corrected text.", "fast", "Paste failed")
    row = store.list_recovery_items()[0]
    assert row["id"] == item_id
    assert row["final_text"] == "Usable corrected text."
    assert "raw_text" not in row
    assert "audio" not in row
    store.clear_recovery_items()
    assert store.list_recovery_items() == []


def test_recovery_checkpoint_survives_interruption_and_is_removed_after_success(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    item_id = store.checkpoint_recovery("session-1", "Stable local words", "fast")
    row = store.list_recovery_items()[0]
    assert row["id"] == item_id
    assert row["status"] == "recording"
    store.checkpoint_recovery(
        "session-1",
        "Stable local words updated",
        "fast",
        status="failed",
        error_message="App interrupted",
    )
    rows = store.list_recovery_items()
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert rows[0]["final_text"] == "Stable local words updated"
    store.delete_recovery_session("session-1")
    assert store.list_recovery_items() == []


def test_schema_v3_recovery_table_migrates_without_losing_text(tmp_path):
    database = tmp_path / "echo-link.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript(
            """
            CREATE TABLE recovery_items(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                final_text TEXT NOT NULL,
                mode TEXT NOT NULL,
                error_message TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            INSERT INTO recovery_items(final_text, mode, error_message, created_at)
            VALUES('Saved before upgrade.', 'fast', 'Paste failed', '2026-07-18T00:00:00+00:00');
            """
        )
    store = EchoLinkStore(database)
    row = store.list_recovery_items()[0]
    assert row["final_text"] == "Saved before upgrade."
    assert row["status"] == "failed"
    assert row["session_key"] is None


def test_schema_v5_migrates_to_v8_without_losing_vocabulary(tmp_path):
    database = tmp_path / "echo-link.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta(key, value) VALUES('schema_version', '5');
            CREATE TABLE vocabulary(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                spoken_form TEXT NOT NULL COLLATE NOCASE UNIQUE,
                written_form TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'custom',
                created_at TEXT NOT NULL
            );
            INSERT INTO vocabulary(spoken_form, written_form, category, created_at)
            VALUES('echo link', 'Echo-Link', 'product', '2026-07-19T00:00:00+00:00');
            """
        )
    store = EchoLinkStore(database)
    assert store.get_meta("schema_version") == 8
    assert store.vocabulary_map()["echo link"] == "Echo-Link"
    assert store.list_learned_corrections() == []
    document_id = store.create_document("Migrated workspace", "Local text")
    assert store.get_document(document_id)["content"] == "Local text"


def test_legacy_import_is_selective_and_one_time(tmp_path):
    legacy = tmp_path / "legacy.db"
    with closing(sqlite3.connect(legacy)) as connection:
        connection.executescript(
            """
            CREATE TABLE settings(key TEXT, value TEXT);
            CREATE TABLE dictionary(spoken_form TEXT, written_form TEXT, category TEXT);
            """
        )
        connection.executemany(
            "INSERT INTO settings(key, value) VALUES(?, ?)",
            [
                ("dictation_hotkey", "ctrl+f9"),
                ("dictation_language", "en-US"),
                ("ollama_model", "qwen2.5:3b"),
                ("api_key", "must-not-import"),
            ],
        )
        connection.execute(
            "INSERT INTO dictionary(spoken_form, written_form, category) VALUES(?, ?, ?)",
            ("curtis", "Curtis", "name"),
        )
        connection.commit()

    store = EchoLinkStore(tmp_path / "new.db")
    result = store.import_legacy_dictation(legacy)
    assert result == {"settings": 3, "vocabulary": 1}
    assert store.get_settings().hotkey == "ctrl+f9"
    assert store.get_settings().language == "en"
    assert store.get_settings().ollama_model == "gemma4:12b"
    assert store.vocabulary_map()["curtis"] == "Curtis"
    assert store.import_legacy_dictation(legacy) == {"settings": 0, "vocabulary": 0}


def test_legacy_import_rejects_remote_or_wrong_type_sources(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")

    with pytest.raises(ValueError, match="Network legacy dictation database"):
        store.import_legacy_dictation(r"\\server\share\dictation.db")
    with pytest.raises(ValueError, match="must use one of"):
        store.import_legacy_dictation(tmp_path / "legacy.sqlite")
