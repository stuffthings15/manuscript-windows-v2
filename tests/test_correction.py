from __future__ import annotations

import json
from pathlib import Path

from echo_link_core.correction import (
    CorrectionPipeline,
    CorrectionResult,
    _meaning_guard,
    deterministic_cleanup,
)
from echo_link_core.models import DictationMode, DictationSettings


class StubRefiner:
    def __init__(self, response: CorrectionResult):
        self.response = response

    def refine(self, *_args, **_kwargs):
        return self.response

    def available_models(self, _timeout=1.5):
        return {"qwen2.5:3b"}


def test_settings_enforce_five_minute_local_model_idle_unload():
    assert DictationSettings().gpu_idle_release_seconds == 300
    assert (
        DictationSettings.from_dict({"gpu_idle_release_seconds": 120})
        .gpu_idle_release_seconds
        == 300
    )


def test_expanded_speech_cleanup_corpus():
    path = Path(__file__).resolve().parents[1] / "docs" / "expanded_speech_cleanup.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert len(payload["cases"]) >= 14
    for case in payload["cases"]:
        assert deterministic_cleanup(case["raw"], {}) == case["expected"], case["id"]


def test_cleanup_formats_punctuation_repetition_and_vocabulary():
    text = "um echo link echo link comma please start new paragraph how are you question mark"
    result = deterministic_cleanup(text, {"echo link": "Echo-Link"})
    assert result == "Echo-Link, please start\n\nHow are you?"


def test_cleanup_applies_self_correction():
    assert deterministic_cleanup("send it friday no wait monday", {}) == "Send it monday."


def test_cleanup_adds_question_mark_for_questions():
    assert deterministic_cleanup("can you open settings", {}) == "Can you open settings?"


def test_cleanup_preserves_email_url_path_time_and_filename_tokens():
    vocabulary = {"echo link plan": "echo_link_plan.md"}
    assert deterministic_cleanup("send to jane.doe@example.com", {}) == "Send to jane.doe@example.com."
    assert deterministic_cleanup("open https://echo-link.local/docs", {}) == "Open https://echo-link.local/docs."
    assert deterministic_cleanup(r"open C:\Work\notes.txt", {}) == r"Open C:\Work\notes.txt."
    assert deterministic_cleanup("schedule 2:30 pm", {}) == "Schedule 2:30 pm."
    assert deterministic_cleanup("save echo link plan", vocabulary) == "Save echo_link_plan.md."


def test_cleanup_formats_repeated_newline_phrases_as_a_list():
    assert deterministic_cleanup(
        "buy milk new line buy bread new line buy coffee", {}
    ) == "- Buy milk\n- Buy bread\n- Buy coffee."


def test_cleanup_applies_natural_replacement_and_safe_now_boundary():
    assert deterministic_cleanup(
        "set the count to two actually make that three", {}
    ) == "Set the count to three."
    assert deterministic_cleanup(
        "the build passed now create the installer", {}
    ) == "The build passed. Now create the installer."


def test_cleanup_splits_only_clear_run_on_questions_and_actions():
    assert deterministic_cleanup(
        "the report is ready can you review it", {}
    ) == "The report is ready. Can you review it?"
    assert deterministic_cleanup(
        "the deployment failed why did the service stop", {}
    ) == "The deployment failed. Why did the service stop?"
    assert deterministic_cleanup(
        "save the draft next send it to review", {}
    ) == "Save the draft. Next send it to review."
    assert deterministic_cleanup(
        "close the file after that run the tests", {}
    ) == "Close the file. After that run the tests."


def test_cleanup_run_on_split_preserves_protected_content():
    assert deterministic_cleanup(
        "the report at https://echo-link.local/docs is ready can you email jane.doe@example.com",
        {},
    ) == (
        "The report at https://echo-link.local/docs is ready. "
        "Can you email jane.doe@example.com?"
    )


def test_cleanup_does_not_split_embedded_questions_or_transition_words():
    assert deterministic_cleanup(
        "tell me whether you can review the next file", {}
    ) == "Tell me whether you can review the next file."
    assert deterministic_cleanup(
        "the question is can you review it", {}
    ) == "The question is can you review it."
    assert deterministic_cleanup(
        "I wonder why did the service stop", {}
    ) == "I wonder why did the service stop."
    assert deterministic_cleanup("open the next file", {}) == "Open the next file."


def test_cleanup_handles_punctuated_backtracking_from_voice_spec():
    assert deterministic_cleanup(
        "meet me at two... actually, make that three", {}
    ) == "Meet me at three."


def test_cleanup_removes_only_unambiguous_extended_fillers():
    assert deterministic_cleanup(
        "uh like, I think this works, you know", {}
    ) == "I think this works."
    assert deterministic_cleanup("I like green apples", {}) == "I like green apples."
    assert deterministic_cleanup("open the file [pause] now save it", {}) == (
        "Open the file. Now save it."
    )


def test_cleanup_formats_spoken_number_sequence_as_list():
    assert deterministic_cleanup(
        "one apples two bananas three oranges", {}
    ) == "- Apples\n- Bananas\n- Oranges"
    assert deterministic_cleanup(
        "first draft second review third publish", {}
    ) == "- Draft\n- Review\n- Publish"


def test_cleanup_formats_explicit_bullet_markers():
    assert deterministic_cleanup(
        "bullet point milk bullet point bread bullet point coffee", {}
    ) == "- Milk\n- Bread\n- Coffee"


def test_spoken_question_mark_does_not_duplicate_model_punctuation():
    assert deterministic_cleanup("can you open settings question mark?", {}) == "Can you open settings?"


def test_fast_mode_never_calls_refiner():
    refiner = StubRefiner(CorrectionResult("Changed.", "ollama"))
    settings = DictationSettings(mode=DictationMode.FAST, append_trailing_space=False)
    pipeline = CorrectionPipeline(lambda: {}, settings, refiner)
    assert pipeline.correct("hello there").text == "Hello there."


def test_developer_mode_is_exact_deterministic_and_never_calls_refiner():
    class ForbiddenRefiner:
        def refine(self, *_args, **_kwargs):
            raise AssertionError("Developer mode must not call a local language model")

    settings = DictationSettings(
        mode=DictationMode.DEVELOPER,
        append_trailing_space=False,
        local_refinement_enabled=True,
    )
    pipeline = CorrectionPipeline(
        lambda: {"echo link core": "echo_link_core"},
        settings,
        ForbiddenRefiner(),
    )
    result = pipeline.correct(
        "def camel case get user open paren close paren colon new line "
        "indent return tag file echo link core slash models dot py"
    )
    assert result.text == "def getUser():\n    return @echo_link_core/models.py"
    assert result.source == "developer"


def test_developer_mode_does_not_apply_prose_learning_rules_to_code():
    settings = DictationSettings(
        mode=DictationMode.DEVELOPER,
        append_trailing_space=False,
        personal_learning_enabled=True,
    )
    pipeline = CorrectionPipeline(
        lambda: {},
        settings,
        learned_correction_provider=lambda: {"teh": "the"},
    )
    assert pipeline.deterministic("teh equals one").text == "teh = one"


def test_snippet_command_returns_exact_local_content_without_refinement():
    settings = DictationSettings(append_trailing_space=False)
    pipeline = CorrectionPipeline(
        lambda: {},
        settings,
        snippet_provider=lambda: {"email signature": "Regards,\nCurtis"},
    )
    result = pipeline.correct("insert snippet email signature")
    assert result.text == "Regards,\nCurtis"
    assert result.source == "snippet"


def test_rejected_refinement_keeps_deterministic_text():
    refiner = StubRefiner(CorrectionResult("Hello there.", "deterministic", "not available"))
    settings = DictationSettings(mode=DictationMode.ACCURACY, append_trailing_space=False)
    pipeline = CorrectionPipeline(lambda: {}, settings, refiner)
    result = pipeline.correct("hello there")
    assert result.text == "Hello there."
    assert result.warning == "not available"


def test_meaning_guard_protects_numbers_and_paths():
    assert _meaning_guard(r"Open C:\Work\file.txt at 2:30", r"Please open C:\Work\file.txt at 2:30.")
    assert not _meaning_guard(r"Open C:\Work\file.txt at 2:30", "Open the file later.")


def test_personal_learning_is_opt_in_and_applies_after_cleanup():
    settings = DictationSettings(
        append_trailing_space=False,
        personal_learning_enabled=True,
    )
    pipeline = CorrectionPipeline(
        lambda: {},
        settings,
        learned_correction_provider=lambda: {"teh report": "the report"},
    )
    assert pipeline.deterministic("send teh report").text == "Send the report."
    settings.personal_learning_enabled = False
    pipeline.update_settings(settings)
    assert pipeline.deterministic("send teh report").text == "Send teh report."


def test_custom_vocabulary_and_sensitive_tokens_win_over_learning():
    settings = DictationSettings(
        append_trailing_space=False,
        personal_learning_enabled=True,
    )
    pipeline = CorrectionPipeline(
        lambda: {"open ai": "OpenAI"},
        settings,
        learned_correction_provider=lambda: {
            "OpenAI": "Open I",
            "example": "sample",
        },
    )
    assert pipeline.deterministic("use open ai").text == "Use OpenAI."
    assert pipeline.deterministic("open https://example.com").text == (
        "Open https://example.com."
    )
