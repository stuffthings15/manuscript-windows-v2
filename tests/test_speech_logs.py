from __future__ import annotations

from datetime import datetime, timedelta, timezone

from echo_link_core.speech_logs import analyze_punctuation
from echo_link_core.storage import EchoLinkStore


def _add_log(store: EchoLinkStore, *, retention_days: int = 15) -> int:
    return store.add_speech_improvement_log(
        raw_text="We should go. to the store",
        deterministic_text="We should go. to the store.",
        suggested_text="We should go. to the store.",
        final_text="We should go to the store.",
        audio_pcm=b"\x01\x00" * 16000,
        sample_rate=16000,
        mode="fast",
        analysis=analyze_punctuation(
            "We should go. to the store",
            "We should go. to the store.",
            "We should go. to the store.",
            "We should go to the store.",
        ),
        retention_days=retention_days,
    )


def test_punctuation_analysis_identifies_stage_and_review_removal():
    report = analyze_punctuation(
        "we should go. to the store",
        "We should go. to the store.",
        "We should go. to the store.",
        "We should go to the store.",
    )
    assert report["counts"]["speech_recognition"] == 1
    assert report["counts"]["removed_during_review"] == 1
    assert report["issues"][0]["origin"] == "speech recognition"
    assert report["issues"][0]["kept_in_final"] is False


def test_encrypted_speech_log_round_trip_review_and_no_plaintext_on_disk(tmp_path):
    database = tmp_path / "echo-link.db"
    store = EchoLinkStore(database)
    item_id = _add_log(store)
    listed = store.list_speech_improvement_logs()
    assert listed[0]["id"] == item_id
    assert "audio_pcm" not in listed[0]
    record = store.get_speech_improvement_log(item_id)
    assert record is not None
    assert len(record["audio_pcm"]) == 32000
    assert record["analysis"]["counts"]["speech_recognition"] == 1
    store.update_speech_improvement_log(
        item_id,
        punctuation_wrong=True,
        user_notes="Period split one sentence.",
        review_decision="user_reviewed",
    )
    reviewed = store.list_speech_improvement_logs()[0]
    assert reviewed["punctuation_wrong"] == 1
    assert reviewed["user_notes"] == "Period split one sentence."
    encrypted_bytes = b"".join(
        path.read_bytes() for path in tmp_path.glob("echo-link.db*") if path.is_file()
    )
    assert b"We should go" not in encrypted_bytes
    assert b"Period split one sentence" not in encrypted_bytes


def test_speech_log_retention_is_capped_and_expired_rows_are_purged(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    item_id = _add_log(store, retention_days=90)
    record = store.get_speech_improvement_log(item_id)
    assert record is not None
    created = datetime.fromisoformat(record["created_at"])
    expires = datetime.fromisoformat(record["expires_at"])
    assert expires - created == timedelta(days=15)
    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with store._connect() as connection:
        connection.execute(
            "UPDATE speech_improvement_logs SET expires_at=? WHERE id=?",
            (expired, item_id),
        )
    assert store.list_speech_improvement_logs() == []
    with store._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM speech_improvement_logs"
        ).fetchone()[0] == 0
