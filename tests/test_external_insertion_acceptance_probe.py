from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import external_insertion_acceptance_probe as probe


class _PassingRunner:
    process_elevated = False

    def run_standard_textbox(self):
        return {
            "passed": True,
            "target_kind": "external_standard_textbox",
            "exact_text_match": True,
        }

    def run_chat_textbox(self):
        return {
            "passed": True,
            "target_kind": "external_chat_like_textbox",
            "exact_text_match": True,
            "chat_not_submitted": True,
        }

    def run_terminal(self):
        return {
            "passed": True,
            "target_kind": "external_windows_command_terminal",
            "exact_marker_match": True,
            "safe_echo_command_verified": True,
        }


def test_collect_evidence_separates_automated_passes_from_manual_gates():
    report = probe.collect_evidence(
        _PassingRunner(),
        provenance={
            "source_commit": "a" * 40,
            "source_tree_clean_at_probe_start": True,
        },
        started_at="2026-07-19T00:00:00+00:00",
    )

    assert report["status"] == "passed"
    assert report["local_only"] is True
    assert report["network_used"] is False
    assert report["user_data_deleted_or_overwritten"] is False
    assert [item["id"] for item in report["automated_cases"]] == list(
        probe.AUTOMATED_CASE_IDS
    )
    assert all(item["status"] == "passed" for item in report["automated_cases"])
    assert report["summary"] == {
        "automated_total": 3,
        "automated_passed": 3,
        "automated_failed": 0,
        "manual_gate_count": 3,
        "manual_gates_closed": False,
    }
    assert {item["id"] for item in report["manual_gates"]} == {
        "elevated_window_uipi",
        "physical_microphone_and_bluetooth",
        "third_party_application_matrix",
    }
    assert all(item["status"] == "manual_required" for item in report["manual_gates"])
    elevated = report["manual_gates"][0]
    assert elevated["automatic_elevation_attempted"] is False
    assert "Do not bypass UIPI" in elevated["acceptance"]


def test_collect_evidence_hashes_failure_type_and_omits_private_error():
    private_error = "PRIVATE_TARGET_TITLE_AND_TEXT_MUST_NOT_PERSIST"

    class _FailingRunner(_PassingRunner):
        def run_chat_textbox(self):
            raise RuntimeError(private_error)

    report = probe.collect_evidence(
        _FailingRunner(),
        provenance={
            "source_commit": "b" * 40,
            "source_tree_clean_at_probe_start": False,
        },
    )
    serialized = json.dumps(report, sort_keys=True)

    assert report["status"] == "failed"
    assert report["summary"]["automated_failed"] == 1
    assert "error_type_sha256" in report["automated_cases"][1]
    assert private_error not in serialized


def test_terminal_command_is_bounded_safe_echo_only():
    marker = "ECHOLINK_TERMINAL_ABC123"
    assert probe._terminal_command(marker) == (
        'echo ECHOLINK_TERMINAL_ABC123>"%ECHOLINK_ACCEPTANCE_OUTPUT%"'
    )
    for unsafe in ("too short", "BAD&COMMAND", "bad_lowercase", "A" * 81):
        with pytest.raises(ValueError, match="safe uppercase"):
            probe._terminal_command(unsafe)


def test_clipboard_restore_is_verified_without_persisting_content(monkeypatch):
    clipboard = {"value": "probe value"}
    monkeypatch.setattr(probe.pyperclip, "copy", lambda value: clipboard.update(value=value))
    monkeypatch.setattr(probe.pyperclip, "paste", lambda: clipboard["value"])

    assert probe._restore_clipboard("private clipboard text") is True
    assert clipboard["value"] == "private clipboard text"


def test_external_process_verification_uses_real_target_not_venv_launcher(monkeypatch):
    monkeypatch.setattr(probe.os, "getpid", lambda: 100)

    assert probe._external_process_verified(300, 300) is True
    assert probe._external_process_verified(200, 300) is False
    assert probe._external_process_verified(100, 100) is False
    assert probe._external_process_verified(0, 0) is False


def test_atomic_report_writer_replaces_in_same_folder(tmp_path, monkeypatch):
    output = tmp_path / "acceptance.json"
    output.write_text("old", encoding="utf-8")
    calls = []
    real_replace = probe.os.replace

    def replace(source, destination):
        calls.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(probe.os, "replace", replace)
    probe._write_json_atomic(output, {"status": "passed"})

    assert json.loads(output.read_text(encoding="utf-8")) == {"status": "passed"}
    assert len(calls) == 1
    temporary, destination = calls[0]
    assert temporary.parent == output.parent
    assert destination == output.resolve()
    assert not temporary.exists()


def test_source_provenance_keeps_only_commit_and_clean_flag(monkeypatch):
    responses = iter(
        (
            SimpleNamespace(returncode=0, stdout="C" * 40 + "\n", stderr=""),
            SimpleNamespace(
                returncode=0,
                stdout=" M PRIVATE_USER_FILE_NAME.txt\n",
                stderr="",
            ),
        )
    )
    monkeypatch.setattr(
        probe.subprocess,
        "run",
        lambda *_args, **_kwargs: next(responses),
    )

    provenance = probe._source_provenance(Path("local-repo"))

    assert provenance == {
        "source_commit": "c" * 40,
        "source_tree_clean_at_probe_start": False,
    }
    assert "PRIVATE_USER_FILE_NAME" not in json.dumps(provenance)


def test_committed_live_evidence_has_three_passes_and_open_manual_gates():
    evidence_path = probe.ROOT / "evidence" / "windows-external-insertion-acceptance.json"
    if not evidence_path.is_file():
        pytest.skip("Live acceptance evidence has not been recorded yet.")
    report = json.loads(evidence_path.read_text(encoding="utf-8"))

    assert report["schema_version"] == probe.SCHEMA_VERSION
    assert report["probe"] == "windows_external_insertion_acceptance"
    assert report["status"] == "passed"
    assert report["provenance"]["source_tree_clean_at_probe_start"] is True
    assert len(report["provenance"]["source_commit"]) == 40
    assert report["summary"]["automated_total"] == 3
    assert report["summary"]["automated_passed"] == 3
    assert report["summary"]["manual_gates_closed"] is False
    assert [item["id"] for item in report["automated_cases"]] == list(
        probe.AUTOMATED_CASE_IDS
    )
    assert all(item["status"] == "passed" for item in report["automated_cases"])
    forbidden_keys = {
        "actual_text",
        "expected_text",
        "command",
        "output_path",
        "window_title",
    }
    for case in report["automated_cases"]:
        assert not forbidden_keys & set(case)
        assert len(case["expected_utf8_sha256"]) == 64
        assert len(case["actual_utf8_sha256"]) == 64
    assert all(item["status"] == "manual_required" for item in report["manual_gates"])
