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
    assert result["score"] == 100.0
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

    writer = probe.build_manuscript_writer("qwen3:8b")

    assert isinstance(writer, polish.PolishWritingAssistantAdapter)


def test_probe_routes_gemma4_to_the_signed_gemma_pack():
    probe = importlib.import_module("tools.gpt56sol_five_page_probe")

    writer = probe.build_manuscript_writer("gemma4:12b")

    assert writer.engine.preferred_pack_provider() == "gemma4-12b-q4-k-m-v1"
