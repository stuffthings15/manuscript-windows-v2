from __future__ import annotations

import json
import hashlib
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from acceptance_evidence import validate_batch  # noqa: E402


def fixture(tmp_path, **changes):
    row = {"essay": "one", "presses": 2, "dismissed": 0,
           "stopped_because": "the review finished after press 2; section 2/2"}
    artifact = tmp_path / "saved.docx"
    artifact.write_bytes(b"saved document")
    row.update(saved_document=str(artifact), saved_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest())
    row.update(changes)
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps([row]), encoding="utf-8")
    (tmp_path / "one.docx").write_bytes(b"saved document")
    return summary


def test_complete_batch(tmp_path):
    assert validate_batch(fixture(tmp_path), {"one.docx"}, tmp_path, 0)["status"] == "passed"


@pytest.mark.parametrize("changes", [
    {"presses": 0}, {"timed_out": True}, {"error": "COM failed"},
    {"dismissed": 1}, {"stalled_at": 1}, {"stopped_because": ""},
    {"stopped_because": "no HUD after press 2; section 2/2"},
])
def test_failed_or_unacknowledged_work_cannot_pass(tmp_path, changes):
    assert validate_batch(fixture(tmp_path, **changes), {"one.docx"}, tmp_path, 0)["status"] == "incomplete"


def test_missing_essay_cannot_be_hidden_by_good_scores(tmp_path):
    assert validate_batch(fixture(tmp_path), {"one.docx", "two.docx"}, tmp_path, 0)["status"] == "invalid"


def test_duplicate_result_is_not_coverage(tmp_path):
    summary = fixture(tmp_path)
    rows = json.loads(summary.read_text())
    summary.write_text(json.dumps(rows * 2))
    assert validate_batch(summary, {"one.docx"}, tmp_path, 0)["status"] == "invalid"


def test_stale_summary(tmp_path):
    summary = fixture(tmp_path)
    assert validate_batch(summary, {"one.docx"}, tmp_path, summary.stat().st_mtime + 1)["status"] == "invalid"


def test_missing_summary(tmp_path):
    assert validate_batch(tmp_path / "absent", {"one.docx"}, tmp_path, 0)["status"] == "invalid"


def test_missing_saved_document(tmp_path):
    summary = fixture(tmp_path)
    (tmp_path / "one.docx").unlink()
    assert validate_batch(summary, {"one.docx"}, tmp_path, 0)["status"] == "incomplete"


@pytest.mark.parametrize("payload", [{}, [None], [{"essay": []}]])
def test_malformed_summary(tmp_path, payload):
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps(payload))
    assert validate_batch(summary, {"one.docx"}, tmp_path, 0)["status"] == "invalid"


def test_missing_build_identity_refuses_measurement(monkeypatch):
    import harness_state
    import release_acceptance
    monkeypatch.setattr(harness_state, "read_state", lambda: {"build": ""})
    monkeypatch.setattr(release_acceptance, "_checkout_build", lambda: "123")
    assert "identity missing" in release_acceptance._stale_app_reason()


def test_stale_build_refuses_measurement(monkeypatch):
    import harness_state
    import release_acceptance
    monkeypatch.setattr(harness_state, "read_state", lambda: {"build": "old"})
    monkeypatch.setattr(release_acceptance, "_checkout_build", lambda: "new")
    assert "restart" in release_acceptance._stale_app_reason()


def test_grading_is_blocked_by_missing_evidence(tmp_path, monkeypatch):
    import release_acceptance
    import animal_essay_corpus_probe
    monkeypatch.setattr(release_acceptance, "SUMMARY", tmp_path / "missing.json")
    monkeypatch.setattr(animal_essay_corpus_probe, "_essay_files", lambda _: [tmp_path / "one.docx"])
    result = release_acceptance._graded(tmp_path, 1, 1, 0)
    assert result["evidence"]["status"] == "invalid"
    assert "mean" not in result


def test_full_tier_does_not_run_after_failed_prerequisite(tmp_path, monkeypatch):
    import release_acceptance
    monkeypatch.setattr(release_acceptance, "EVIDENCE", tmp_path)
    monkeypatch.setattr(release_acceptance, "fast_tier", lambda *_: ["missing proof"])
    def forbidden(*_):
        raise AssertionError("full tier must not take the desktop after failed preflight")
    monkeypatch.setattr(release_acceptance, "full_tier", forbidden)
    monkeypatch.setattr(sys, "argv", ["release_acceptance", "--full"])
    assert release_acceptance.main() == 1
    report = json.loads(next(tmp_path.rglob("acceptance-full.json")).read_text())
    assert report["passed"] is False
    assert report["full_tier_status"].startswith("skipped")


def test_tones_weight_sections_across_documents():
    from acceptance_evidence import aggregate_tones
    result = aggregate_tones([
        {"tone": "Clear", "sections": 1, "passed": 0},
        {"tone": "Clear", "sections": 9, "passed": 9},
    ], 2, "Clear")
    assert result["percent"] == 90


@pytest.mark.parametrize("row", [{}, {"tone": "Clear", "sections": 0, "passed": 0},
    {"tone": "Friendly", "sections": 2, "passed": 2},
    {"tone": "Clear", "sections": 2, "passed": 3}])
def test_bad_judge_reports_do_not_become_a_score(row):
    from acceptance_evidence import aggregate_tones
    assert aggregate_tones([row], 1, "Clear") == {"status": "invalid"}


def test_missing_tone_document_blocks_aggregate():
    from acceptance_evidence import aggregate_tones
    assert aggregate_tones([], 20, "Clear") == {"status": "invalid"}


def test_short_source_corpus_cannot_reduce_the_gate(tmp_path, monkeypatch):
    import release_acceptance
    import animal_essay_corpus_probe
    monkeypatch.setattr(animal_essay_corpus_probe, "_essay_files", lambda _: [tmp_path / "one.docx"])
    assert "missing documents" in release_acceptance._graded(tmp_path, 1, 20, 0)["error"]


@pytest.mark.parametrize("damage", ["missing", "changed", "graded", "identity", "stale"])
def test_saved_evidence_must_match_the_document_being_graded(tmp_path, damage):
    import os
    summary = fixture(tmp_path)
    artifact = tmp_path / "saved.docx"
    if damage == "missing":
        artifact.unlink()
    elif damage == "changed":
        artifact.write_bytes(b"later edits")
    elif damage == "graded":
        (tmp_path / "one.docx").write_bytes(b"different document")
    elif damage == "identity":
        rows = json.loads(summary.read_text())
        rows[0].pop("saved_sha256")
        summary.write_text(json.dumps(rows))
    else:
        os.utime(artifact, (1, 1))
    assert validate_batch(summary, {"one.docx"}, tmp_path, 2)["status"] == "incomplete"
