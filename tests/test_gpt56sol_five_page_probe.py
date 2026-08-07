from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest


def test_gpt56sol_corpus_has_five_error_riddled_pages():
    probe = importlib.import_module("tools.gpt56sol_five_page_probe")

    assert len(probe.CORPUS) == 5
    for page in probe.CORPUS:
        assert len(page.source.split()) >= 300
        assert "\u2014" not in page.source
        defects = probe.scan_source_defects(page.source)
        assert all(
            defects[category] > 0
            for category in (
                "spelling",
                "grammar",
                "punctuation",
                "vocabulary",
                "sentence_structure",
            )
        )
        assert page.protected_terms


def test_gpt56sol_suite_covers_proofread_and_every_visible_style():
    probe = importlib.import_module("tools.gpt56sol_five_page_probe")
    tones = importlib.import_module("tools.full_page_tone_quality_probe").TONES

    assert probe.MODES == ("Proofread", *tones)


def test_static_output_gate_requires_no_known_errors_or_em_dashes():
    probe = importlib.import_module("tools.gpt56sol_five_page_probe")
    clean = (
        "The report is clear, correct, and complete. "
        "Jordan Lee will submit Form AX-17 on October 12."
    )

    result = probe.static_output_gate(
        clean,
        protected_terms=("Jordan Lee", "Form AX-17", "October 12"),
    )

    assert result["passed"]
    assert result["check_type"] == "static_heuristic"
    assert result["static_check_score_percent"] == 100.0
    assert result["independent_model_judgment"] is False
    assert "score" not in result
    assert result["em_dash_count"] == 0


def test_static_output_gate_does_not_flag_valid_subordinate_clauses():
    probe = importlib.import_module("tools.gpt56sol_five_page_probe")
    clean = (
        "Participation fell because the survey was only available in English. "
        "While the clinic remained open, staff checked the generator. "
        "While waiting for the refresh, the analyst checked the queue. "
        "The ingestion service started duplicating events. "
        "Several sentences in the postmortem are fragments; others are complete. "
        "The engineer wrote that data was corrupt; this vocabulary is inaccurate. "
        "The analyst ran audit_invoice_delta.sql against a replica."
    )

    result = probe.static_output_gate(clean, protected_terms=())

    assert result["passed"]


def test_static_output_gate_allows_valid_however_transition():
    probe = importlib.import_module("tools.gpt56sol_five_page_probe")
    clean = (
        "Community gardens can help; however, this claim needs evidence. "
        "Attendance was low; however, this is not proof of poor management."
    )

    result = probe.static_output_gate(clean, protected_terms=())

    assert result["passed"]


def test_page_runner_uses_ordered_hud_sections_and_preserves_gaps():
    probe = importlib.import_module("tools.gpt56sol_five_page_probe")

    class Writer:
        def __init__(self):
            self.calls = []

        def run(self, text, **options):
            self.calls.append((text, options))
            return SimpleNamespace(
                text=text.replace("bad", "good").replace("Bad", "Good")
            )

    source = (
        "bad one. bad two. bad three.\n\n"
        "bad four. bad five. bad six. bad seven."
    )
    writer = Writer()

    output, sections = probe.run_page_through_hud(
        writer,
        source=source,
        command="Proofread",
        model="local-model",
        protected_terms=("bad four", "absent"),
        timeout=30,
    )

    assert output == (
        "Good one. Good two. Good three.\n\n"
        "Good four. Good five. Good six. Good seven."
    )
    # Five-sentence batches: the three-sentence paragraph and the
    # four-sentence paragraph each review as one section.
    assert len(writer.calls) == 2
    assert [section["index"] for section in sections] == [1, 2]
    assert all(section["total"] == 2 for section in sections)
    assert writer.calls[1][1]["protected_terms"] == ("bad four",)
    assert "\n\n" in output


def test_page_runner_reports_the_exact_failed_hud_section():
    probe = importlib.import_module("tools.gpt56sol_five_page_probe")

    class Writer:
        def __init__(self):
            self.last_correction_result = None

        def run(self, text, **options):
            self.last_correction_result = SimpleNamespace(
                review_ready=False,
                quality_complete=False,
                used_llm=True,
                rejected_llm=True,
                message="AI output did not pass final quality checks.",
                residual_errors=("tone contract failed",),
                validation=SimpleNamespace(failures=("actor changed",)),
            )
            return SimpleNamespace(text=text)

    with pytest.raises(
        RuntimeError,
        match=r"HUD section 1 of 1.*tone contract failed.*actor changed",
    ):
        probe.run_page_through_hud(
            Writer(),
            source="This sentence needs work.",
            command="Caveman",
            model="local-model",
            protected_terms=(),
            timeout=30,
        )


def test_page_runner_rejects_safe_but_incomplete_tone_output():
    probe = importlib.import_module("tools.gpt56sol_five_page_probe")

    class Writer:
        def __init__(self):
            self.last_correction_result = None

        def run(self, text, **options):
            self.last_correction_result = SimpleNamespace(
                review_ready=True,
                quality_complete=False,
                tone_verified=False,
                message="Best safe correction ready for review.",
                residual_errors=(),
                validation=SimpleNamespace(failures=()),
            )
            return SimpleNamespace(text="They haven't completed the review.")

    with pytest.raises(RuntimeError, match=r"HUD section 1 of 1"):
        probe.run_page_through_hud(
            Writer(),
            source="They have not completed the review.",
            command="Formal",
            model="local-model",
            protected_terms=(),
            timeout=30,
        )


def test_probe_builds_the_real_adaptive_polish_adapter():
    probe = importlib.import_module("tools.gpt56sol_five_page_probe")
    polish = importlib.import_module("echo_link_core.polish")

    writer = probe.build_manuscript_writer("auto")

    assert isinstance(writer, polish.PolishWritingAssistantAdapter)


def test_probe_routes_each_gemma4_size_to_its_signed_pack():
    probe = importlib.import_module("tools.gpt56sol_five_page_probe")

    expected = {
        "gemma4:e2b-it-qat": "gemma4-e2b-qat-v1",
        "gemma4:e4b-it-qat": "gemma4-e4b-qat-v1",
        "gemma4:12b-it-q4_K_M": "gemma4-12b-q4-k-m-v1",
        "auto": "",
    }
    for model, pack_id in expected.items():
        writer = probe.build_manuscript_writer(model)
        assert writer.engine.preferred_pack_provider() == pack_id


def test_report_cannot_claim_completion_without_independent_judgment():
    probe = importlib.import_module("tools.gpt56sol_five_page_probe")
    report = {
        "schema_version": 1,
        "judge_model": "GPT-5.6 Sol",
        "threshold_percent": 100.0,
        "runtime_passed": True,
        "runtime_passed_count": 1,
        "cases": [
            {
                "mode": "Proofread",
                "page_id": "page-1",
                "runtime_passed": True,
                "gpt56sol_judgment": None,
            }
        ],
    }

    probe._update_report_summary(report, expected_case_count=1)

    assert report["schema_version"] == 2
    assert "judge_model" not in report
    assert report["independent_judge"]["requested_model"] == "GPT-5.6 Sol"
    assert report["runtime_static_passed"] is True
    assert report["independent_judged_count"] == 0
    assert report["independent_judgment_complete"] is False
    assert report["final_passed"] is False
    assert report["final_score_percent"] is None
    assert report["completion_status"] == (
        "awaiting_independent_gpt56sol_judgment"
    )


def test_placeholder_judgment_cannot_unlock_final_gate():
    probe = importlib.import_module("tools.gpt56sol_five_page_probe")
    report = {
        "cases": [
            {
                "mode": "Proofread",
                "page_id": "page-1",
                "runtime_static_passed": True,
                "independent_gpt56sol_judgment": {
                    "passed": True,
                    "score_percent": 100.0,
                },
            }
        ]
    }

    probe._update_report_summary(report, expected_case_count=1)

    assert report["independent_judged_count"] == 0
    assert report["final_passed"] is False
    assert report["final_score_percent"] is None
    assert report["independent_judgment_errors"][0]["error"] == (
        "status is not completed"
    )


def test_completed_independent_judgment_can_unlock_final_gate():
    probe = importlib.import_module("tools.gpt56sol_five_page_probe")
    report = {
        "cases": [
            {
                "mode": "Proofread",
                "page_id": "page-1",
                "runtime_static_passed": True,
                "independent_gpt56sol_judgment": {
                    "status": "completed",
                    "judge_model": "GPT-5.6 Sol",
                    "independent": True,
                    "judge_run_id": "judge-run-1",
                    "judged_at": "2026-08-04T12:00:00+00:00",
                    "score_percent": 100.0,
                    "passed": True,
                    "findings": [],
                },
            }
        ]
    }

    probe._update_report_summary(report, expected_case_count=1)

    assert report["independent_judged_count"] == 1
    assert report["independent_judgment_complete"] is True
    assert report["independent_judgment_passed"] is True
    assert report["final_passed"] is True
    assert report["final_score_percent"] == 100.0
    assert report["completion_status"] == "passed"


def test_cli_fails_while_independent_judgment_is_pending(
    monkeypatch,
):
    probe = importlib.import_module("tools.gpt56sol_five_page_probe")
    pending = {
        "runtime_static_passed_count": 1,
        "case_count": 1,
        "independent_judged_count": 0,
        "completion_status": "awaiting_independent_gpt56sol_judgment",
        "final_passed": False,
    }
    monkeypatch.setattr(probe, "run_probe", lambda **_options: pending)

    exit_code = probe.main(
        [
            "--modes",
            "Proofread",
            "--pages",
            "page-1",
            "--output",
            "unused-report.json",
        ]
    )

    assert exit_code == 1
