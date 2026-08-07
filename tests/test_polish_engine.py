from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from echo_link_core.polish import (
    PolishEngine,
    PolishWritingAssistantAdapter,
    _actor_counter,
    _apply_safe_concise_reductions,
    _concise_rewrite_is_visible,
    _named_entities,
    _apply_safe_cooperative_signal,
    _restore_rephrase_content_nouns,
    _restore_source_phrase_casing,
)
from echo_link_core.polish_hardware import BenchmarkRecord, PolishBenchmarkCache
from echo_link_core.polish_quality import TONE_CONTRACTS
from echo_link_core.polish_model_packs import (
    ModelPackConsentRequired,
    ModelPackDownloadError,
    ModelPackSecurityError,
)
from echo_link_core.polish_runtime import AdaptiveInferenceRuntime
from echo_link_core.polish_spelling import DeterministicPolishResult
from echo_link_core.polish_types import (
    AcceptanceProbeResult,
    ClassifierDecision,
    ContextScope,
    CorrectionRequest,
    CorrectionResult,
    ExecutionPlan,
    GenerationResult,
    HardwareSnapshot,
    PolishMode,
    PolishModelPack,
    PolishTask,
    RuntimeBackend,
    RuntimeFailureKind,
    RuntimeStatus,
    ValidationResult,
)


GIB = 1024**3








def test_concise_allows_short_named_fact_dense_sentence():
    source = (
        "The researchers corrected that problem on April 22 by adding translated "
        "forms and hiring Linh Tran and Mateo Silva as paid outreach coordinators."
    )

    assert _concise_rewrite_is_visible(source, source)
def test_concise_allows_short_already_compact_fact_dense_sentence():
    source = (
        "The Lakeside University research group studied twelve gardens between "
        "March 2024 and February 2025."
    )

    assert _concise_rewrite_is_visible(source, source)


def test_concise_rejects_unchanged_short_filler_sentence():
    source = "The team basically completed the review in a very careful way."

    assert not _concise_rewrite_is_visible(source, source)
def test_concise_allows_corrected_dense_fact_section_without_forced_deletion():
    source = (
        "Elena must keep DG32-LAUNCH until November 16, and the final note must "
        "list 126 technicians and seven districts."
    )

    assert _concise_rewrite_is_visible(source, source)

def test_concise_allows_short_requirement_and_prohibition_unchanged():
    source = (
        "Owners remain required in the final table, and no customer data may appear "
        "in public."
    )

    assert _concise_rewrite_is_visible(source, source)

def test_concise_short_fact_dense_section_requires_one_word_reduction():
    source = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty twentyone twentytwo"
    candidate = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty twentyone"

    assert _concise_rewrite_is_visible(source, candidate)


def test_safe_concise_reductions_compact_report_meta_language():
    source = (
        "There are also several punctuation and structure problems in the preliminary report. "
        "Another paragraph repeats the same conclusion three times in slightly different words. "
        "Readers get confused about the actual sequence."
    )

    assert _apply_safe_concise_reductions(source) == (
        "The preliminary report has several punctuation and structural problems. "
        "Another paragraph repeats the conclusion three times in slightly different words. "
        "Readers get confused about the sequence."
    )


@pytest.mark.parametrize(
    ("command", "source", "candidate", "expected"),
    (
        (
            "Friendly",
            "The report is ready.",
            "The report is ready.",
            "friendly rewrite is not visibly warm",
        ),
        (
            "Diplomatic",
            "The report needs revision.",
            "You failed, and this ridiculous report is your fault.",
            "diplomatic rewrite contains hostile wording",
        ),
        (
            "Empathetic",
            "The outage affected users.",
            "The outage affected users.",
            "empathetic rewrite does not make care visible",
        ),
        (
            "Rephrase",
            "The team completed the report.",
            "The team completed the report.",
            "rephrase did not change sentence construction",
        ),
        (
            "Professional",
            "The report is ready.",
            "You guys got a bunch of work done.",
            "professional rewrite contains casual wording",
        ),
    ),
)
def test_source_aware_tone_gate_rejects_obvious_style_misses(
    command,
    source,
    candidate,
    expected,
):
    value, _ = engine(classifier=FakeClassifier(), runtime=FakeRuntime(()))

    quality = value._evaluate_quality(source, candidate, command=command)

    assert not quality.complete
    assert not quality.tone_verified
    assert expected in quality.residual_errors


_MECHANICAL_TONE_PROHIBITION_CASES = (
    pytest.param(
        "Clear",
        "The report does not explain the result.",
        "The report doesnt explain the result.",
        id="clear-broken-grammar",
    ),
    pytest.param(
        "Improve",
        "The report does not explain the result.",
        "The report doesnt explain the result.",
        id="improve-broken-grammar",
    ),
    pytest.param(
        "Rephrase",
        "The team completed the report.",
        "The team completed the report.",
        id="rephrase-copied-source",
    ),
    pytest.param(
        "Shorten",
        "The team basically completed the report in a very careful way.",
        "The team basically completed the report in a very careful way.",
        id="shorten-kept-filler",
    ),
    pytest.param(
        "Friendly",
        "The report is ready.",
        "You guys are gonna love this super easy report.",
        id="friendly-slang",
    ),
    pytest.param(
        "Formal",
        "The report is not ready.",
        "The report isn't ready.",
        id="formal-contraction",
    ),
    pytest.param(
        "Professional",
        "The team completed the report.",
        "You guys got a bunch of work done.",
        id="professional-casual-chatter",
    ),
    pytest.param(
        "Academic",
        "The study is not complete.",
        "The study isn't complete.",
        id="academic-contraction",
    ),
    pytest.param(
        "Conversational",
        "The report explains the result.",
        "Um, like, the report explains the result, you know.",
        id="conversational-filler-overload",
    ),
    pytest.param(
        "Concise",
        "The service does not send private records.",
        "Does not send records.",
        id="concise-telegraphic-fragment",
    ),
    pytest.param(
        "Detailed",
        "The report is ready.",
        "The report report is ready.",
        id="detailed-needless-repetition",
    ),
    pytest.param(
        "Persuasive",
        "The report recommends another review.",
        "You must accept the report now or else.",
        id="persuasive-pressure-tactic",
    ),
    pytest.param(
        "Confident",
        "The report needs revision.",
        "Only an idiot would question this report.",
        id="confident-hostility",
    ),
    pytest.param(
        "Casual",
        "The report is ready.",
        "The widdle report is ready.",
        id="casual-baby-talk",
    ),
    pytest.param(
        "Technical",
        "The service processes records.",
        "The revolutionary best-in-class service processes records.",
        id="technical-marketing-language",
    ),
    pytest.param(
        "Simplified",
        "The report is ready.",
        "The widdle report is ready.",
        id="simplified-childish-wording",
    ),
    pytest.param(
        "High-school level",
        "The report is ready.",
        "The widdle report is ready.",
        id="high-school-childish-wording",
    ),
    pytest.param(
        "College level",
        "The report explains the result.",
        "The aforementioned report elucidates the result.",
        id="college-inflated-wording",
    ),
    pytest.param(
        "Diplomatic",
        "The report needs revision.",
        "This is your fault because you failed.",
        id="diplomatic-blame",
    ),
    pytest.param(
        "Empathetic",
        "The delay affected users.",
        "I am sorry that the delay affected users.",
        id="empathetic-invented-apology",
    ),
    pytest.param(
        "Cooperative",
        "The team will review the report.",
        "The team will work together. We propose a new schedule.",
        id="cooperative-invented-proposal",
    ),
    pytest.param(
        "Assertive",
        "The report needs revision.",
        "Approve this report now or face consequences.",
        id="assertive-threat",
    ),
    pytest.param(
        "Caveman",
        "The report is ready. The review starts next.",
        "- Report ready.\n- Review starts next.",
        id="caveman-list",
    ),
)


def test_mechanical_tone_table_covers_every_visible_contract():
    assert {
        parameter.values[0] for parameter in _MECHANICAL_TONE_PROHIBITION_CASES
    } == set(TONE_CONTRACTS)


@pytest.mark.parametrize(
    ("command", "source", "candidate"),
    _MECHANICAL_TONE_PROHIBITION_CASES,
)
def test_visible_tone_rejects_mechanically_verifiable_prohibited_behavior(
    command,
    source,
    candidate,
):
    value, _ = engine(classifier=FakeClassifier(), runtime=FakeRuntime(()))

    quality = value._evaluate_quality(source, candidate, command=command)

    assert not quality.complete
    assert not quality.tone_verified


def test_concise_allows_short_structured_fact_list_unchanged():
    source = (
        "One paragraph lists soil testing, tool storage, compost training, and "
        "language access without commas."
    )

    assert _concise_rewrite_is_visible(source, source)
def test_safe_concise_reductions_contract_unambiguous_negation():
    source = "The study did not measure property values."

    assert _apply_safe_concise_reductions(source) == (
        "The study didn't measure property values."
    )
def test_safe_concise_reductions_compact_meeting_and_percentage_clauses():
    source = (
        "The group counted attendance at public meetings. They recorded 7,240 pounds "
        "of produce, of which 61 percent went to Community Pantry West."
    )

    assert _apply_safe_concise_reductions(source) == (
        "The group counted public meeting attendance. They recorded 7,240 pounds "
        "of produce; 61 percent went to Community Pantry West."
    )
def test_safe_concise_reductions_compact_quantity_and_language_clauses():
    source = (
        "Quantity by itself does not explain access. This caused fewer responses "
        "from households that primarily spoke Spanish or Vietnamese."
    )

    assert _apply_safe_concise_reductions(source) == (
        "Quantity alone doesn't explain access. This caused fewer responses "
        "from primarily Spanish- or Vietnamese-speaking households."
    )

def test_safe_concise_reductions_preserve_modal_conclusion_claims():
    source = (
        "The conclusion should state that gardens can support local learning when "
        "organizers have reliable resources. Professor Okafor wants the final "
        "wording to remain cautious, transparent, and useful to city staff."
    )

    assert _apply_safe_concise_reductions(source) == (
        "The conclusion should say gardens can support local learning when "
        "organizers have reliable resources. Professor Okafor wants the final "
        "wording to be cautious, transparent, and useful to city staff."
    )

def test_safe_concise_reductions_compact_first_section_patterns():
    source = (
        "The team is preparing a regional power outage drill, but several department "
        "plans were submitted late, and they contain conflicting instructions. The "
        "final review didn't happen as the plan said it would. The first alert occurred "
        "at 02:17 UTC; however, the overnight operator closed it because CPU use was "
        "normal. The proposal does not request permanent hours, and it does not promise "
        "success."
    )

    assert _apply_safe_concise_reductions(source) == (
        "The team plans a regional power outage drill, but several department "
        "plans were submitted late with conflicting instructions. The final review "
        "didn't happen as planned. The overnight operator closed the first alert at "
        "02:17 UTC because CPU use was normal. The proposal doesn't request permanent "
        "hours, and it doesn't promise success."
    )

def test_safe_concise_reductions_compact_interest_explanation():
    source = (
        "The strongest interest came from community college students. This data "
        "should be explained more cautiously. A survey shows interest but does not "
        "guarantee attendance."
    )

    assert _apply_safe_concise_reductions(source) == (
        "The strongest interest was from community college students. This data "
        "should be explained cautiously. A survey shows interest but doesn't "
        "guarantee attendance."
    )

def test_safe_concise_reductions_remove_repeated_arrow_noun():
    source = (
        "He ordered blue arrows for patient areas and orange arrows for supply areas."
    )

    assert _apply_safe_concise_reductions(source) == (
        "He ordered blue arrows for patient areas and orange for supply areas."
    )

def test_safe_concise_reductions_repair_tablet_incident():
    source = (
        "Eight tablets loose their saved routes after the battery went below 12 "
        "percent, the support team marked the issue as cosmetic when it actually "
        "blocked two emergency repairs. Which was a serious mistake."
    )

    assert _apply_safe_concise_reductions(source) == (
        "Eight tablets lost their saved routes after the battery dropped below 12 "
        "percent; the support team marked the issue as cosmetic when it blocked two "
        "emergency repairs, which was a serious mistake."
    )


def test_safe_concise_reductions_compact_invoice_comparison():
    source = (
        "The billing preview showed $286,910.40 more revenue than the source ledger. "
        "Ravi Morgan declared severity SEV-1."
    )

    assert _apply_safe_concise_reductions(source) == (
        "The billing preview exceeded the source ledger by $286,910.40. "
        "Ravi Morgan declared SEV-1."
    )

def test_concise_recovers_whole_tablet_section_after_ai_failure():
    source = (
        "No personal email address can be used, even if a worker says it is more easier. "
        "During the pilot, eight tablets loose their saved routes after the battery "
        "went below 12 percent, the support team marked the issue as cosmetic when it "
        "actually blocked two emergency repairs. Which was a serious mistake."
    )
    unsafe = source.replace("can be used", "is used")
    runtime = FakeRuntime((unsafe, unsafe))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.REWRITE,
            mode=PolishMode.BALANCED,
            command="Concise",
            manual=True,
        )
    )

    assert not result.quality_complete
    assert result.validation.accepted
    assert not result.used_llm
    assert not result.review_ready
    assert "can be used" in result.text
    assert "tablets lost their saved routes" in result.text
    assert "12 percent; the support team" in result.text
    assert "repairs, which was a serious mistake" in result.text


def test_safe_concise_reductions_compact_temperature_range():
    source = "Both units must stay inside their approved temperature ranges."

    assert _apply_safe_concise_reductions(source) == (
        "Both units must stay within approved temperature ranges."
    )

def test_concise_allows_identifier_ordered_fact_dense_section():
    source = (
        "Runbook RB-SYNC-64 says disable the feature flag, drain INV-PRIMARY, restart "
        "two workers, and compare counts before or after producers are paused."
    )

    assert _concise_rewrite_is_visible(source, source)


def test_safe_concise_reductions_clarify_free_to_visitors():
    source = (
        "The proposal also says the program will be completely free, which is incorrect "
        "vocabulary since the library expects operating costs of $18,600. Visitors will "
        "not pay a fee, but the city still pays staff security utilities and "
        "transportation support."
    )

    assert _apply_safe_concise_reductions(source) == (
        "The proposal says the program will be free to visitors, though the library expects "
        "$18,600 in operating costs. Visitors will not pay a fee, but the city still "
        "pays for staff, security, utilities, and transportation support."
    )


def test_proofread_recovers_whole_tablet_section_after_ai_failure():
    source = (
        "No personal email address can be used, even if a worker says it is more easier. "
        "During the pilot, eight tablets loose their saved routes after the battery "
        "went below 12 percent, the support team marked the issue as cosmetic when it "
        "actually blocked two emergency repairs. Which was a serious mistake."
    )
    unsafe = source.replace("can be used", "is used")
    runtime = FakeRuntime((unsafe, unsafe))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.PROOFREAD,
            mode=PolishMode.BALANCED,
            command="Proofread",
            manual=True,
        )
    )

    assert not result.quality_complete
    assert result.validation.accepted
    assert not result.used_llm
    assert not result.review_ready
    assert "can be used" in result.text
    assert "tablets lost their saved routes" in result.text
    assert "12 percent; the support team" in result.text
    assert "repairs, which was a serious mistake" in result.text

def test_possessive_there_typo_is_not_counted_as_actor():
    assert _actor_counter("Both units stay inside there approved ranges.")["they"] == 0


def test_concise_allows_device_and_people_fact_dense_section_one_word_shorter():
    source = (
        "Nurse coordinator Mei Lin monitors FR-22 while technician Carlos Vega "
        "monitors FZ-9 and both units stay inside there approved temperature ranges."
    )
    candidate = source.replace(
        "inside there approved temperature ranges",
        "within approved temperature ranges",
    )

    assert _concise_rewrite_is_visible(source, candidate)

def test_concise_allows_multi_identifier_timeline_unchanged():
    source = (
        "Ravi paused producers at 03:22 UTC. Sofia disabled SB_DEDUP_V2 at "
        "03:24 UTC, and workers sb_retry_1 and sb_retry_2 restarted at 03:29 UTC."
    )

    assert _concise_rewrite_is_visible(source, source)


def test_safe_concise_reductions_repair_training_section():
    source = (
        "Training begins in Conference Room 5C and remote workers joins. The trainer "
        "have forty five minutes to explain route syncing offline notes photo uploads "
        "and emergency escalation. Several instruction is wrote with vague vocabulary, "
        "for example the guide says users should deal with failed uploads in the normal "
        "fashion."
    )

    assert _apply_safe_concise_reductions(source) == (
        "Training begins in Conference Room 5C, and remote workers join. The trainer "
        "has forty-five minutes to explain route syncing, offline notes, photo uploads, "
        "and emergency escalation. Several instructions are written with vague "
        "vocabulary; for example, the guide says users should handle failed uploads "
        "normally."
    )

def test_actor_counter_preserves_collective_group_subject():
    actors = _actor_counter("Instead, the group counted volunteer hours.")

    assert actors["group"] == 1


def test_existential_there_is_not_an_actor_or_named_entity():
    source = "There are several punctuation problems in the report."

    assert _actor_counter(source)["they"] == 0
    assert "There" not in _named_entities(source)
def test_sentence_start_several_is_not_named_entity():
    assert "Several" not in _named_entities(
        "Several sentences in the draft postmortem are fragments."
    )

def test_tone_normalization_restores_source_casing_for_fake_named_entity():
    source = "Community gardens are useful. Lakeside University studied them."
    candidate = "Community Gardens are useful. Lakeside University studied them."

    assert _restore_source_phrase_casing(source, candidate) == source
def test_source_fact_restore_preserves_reporting_tense_and_noun_number():
    source = (
        "Director Amina Shah says the drill is ready. "
        "The printed pocket card reverse the channel numbers."
    )
    candidate = (
        "Director Amina Shah stated that the drill is ready. "
        "The printed pocket cards list the channel numbers in reverse."
    )

    assert _restore_rephrase_content_nouns(
        source,
        candidate,
        command="Improve",
    ) == (
        "Director Amina Shah says that the drill is ready. "
        "The printed pocket card reverses the channel numbers."
    )
    assert _restore_rephrase_content_nouns(
        source,
        (
            "Director Amina Shah stated that the drill is ready. "
            "The printed pocket cards contain the incorrect numbers."
        ),
        command="Improve",
    ) == (
        "Director Amina Shah says that the drill is ready. "
        "The printed pocket card reverses the channel numbers."
    )


def test_source_fact_restore_preserves_relative_time_and_until_relationship():
    source = (
        "Last spring, two visitors entered the hallway; nobody reported the problem "
        "until the exercise had already ended."
    )
    candidate = (
        "During the previous spring, two visitors entered the hallway; nobody "
        "reported the issue after the exercise had already concluded."
    )

    assert _restore_rephrase_content_nouns(
        source,
        candidate,
        command="Rephrase",
    ) == (
        "Last spring, two visitors entered the hallway; nobody reported the issue "
        "until the exercise had already ended."
    )


def test_source_fact_restore_preserves_due_date_relationship():
    source = (
        "The final report is due October 12; it must preserve patient privacy."
    )
    candidate = (
        "The final report must include due October 12; it must preserve patient privacy."
    )

    assert _restore_rephrase_content_nouns(
        source,
        candidate,
        command="Rephrase",
    ) == source


def test_tone_adapter_proofreads_before_applying_requested_style():
    class RecordingEngine:
        def __init__(self):
            self.requests = []

        def analyze(self, value):
            self.requests.append(value)
            output = (
                "The clinic response team is ready."
                if value.command == "Proofread"
                else "Good news, the clinic response team is ready."
            )
            return CorrectionResult(
                original_text=value.text,
                normalized_text=value.text,
                deterministic_text=value.text,
                text=output,
                classifier=ClassifierDecision(
                    needs_llm=True,
                    error_categories=(),
                    severity=1.0,
                    context_scope=ContextScope.SENTENCE,
                    english_confidence=1.0,
                    backend="test",
                    elapsed_ms=1.0,
                ),
                plan=ExecutionPlan(
                    policy_version=1,
                    mode=PolishMode.BALANCED,
                    task=value.task,
                    use_llm=True,
                    model="qwen3:8b",
                    backend=RuntimeBackend.CUDA,
                ),
                validation=ValidationResult(True, 1.0),
                used_llm=True,
                quality_complete=True,
                tone_verified=True,
                quality_score=1.0,
                generation_seconds=1.0,
                review_ready=True,
            )

    engine_value = RecordingEngine()
    adapter = PolishWritingAssistantAdapter(
        engine_value,
        mode_provider=lambda: PolishMode.BALANCED,
        consent_provider=lambda: True,
    )

    result = adapter.run(
        "The clinic response team are ready.",
        command="Friendly",
        protected_terms=("clinic",),
    )

    assert result.text == "Good news, the clinic response team is ready."
    assert [value.command for value in engine_value.requests] == [
        "Proofread",
        "Friendly",
    ]
    assert engine_value.requests[1].text == "The clinic response team is ready."
    assert engine_value.requests[1].preverified_fallback_available
    assert adapter.last_correction_result.original_text == (
        "The clinic response team are ready."
    )
    assert adapter.last_correction_result.generation_seconds == 2.0


def test_adapter_enforces_final_grammar_cleanup_after_ai_output():
    class RecordingEngine:
        def analyze(self, value):
            return CorrectionResult(
                original_text=value.text,
                normalized_text=value.text,
                deterministic_text=value.text,
                text=(
                    "The team was suppose to launch Monday, but the final review "
                    "didn't happen like the plan said it would."
                ),
                classifier=ClassifierDecision(
                    needs_llm=True,
                    error_categories=(),
                    severity=1.0,
                    context_scope=ContextScope.SENTENCE,
                    english_confidence=1.0,
                    backend="test",
                    elapsed_ms=1.0,
                ),
                plan=ExecutionPlan(
                    policy_version=1,
                    mode=PolishMode.BALANCED,
                    task=value.task,
                    use_llm=True,
                    model="gemma4:12b",
                    backend=RuntimeBackend.CUDA,
                ),
                validation=ValidationResult(True, 1.0),
                used_llm=True,
                quality_complete=True,
                tone_verified=True,
                quality_score=1.0,
                review_ready=True,
            )

    adapter = PolishWritingAssistantAdapter(
        RecordingEngine(),
        mode_provider=lambda: PolishMode.BALANCED,
        consent_provider=lambda: True,
    )

    result = adapter.run(
        "The team were suppose to launch Monday, but the final review didnt happen.",
        command="Proofread",
    )

    assert result.text == (
        "The team was supposed to launch Monday, but the final review "
        "did not happen as planned."
    )
    assert adapter.last_correction_result.text == result.text


@pytest.mark.parametrize("command", ["Rewrite", "Improve"])
def test_objective_style_adapter_keeps_verified_ai_proofread_when_rewrite_is_unsafe(
    command,
):
    class RecordingEngine:
        def analyze(self, value):
            proofread = value.command == "Proofread"
            output = (
                "The final report is due October 12; it must preserve patient privacy."
                if proofread
                else "The final report maybe due later, so privacy could change."
            )
            return CorrectionResult(
                original_text=value.text,
                normalized_text=value.text,
                deterministic_text=value.text,
                text=output,
                classifier=ClassifierDecision(
                    needs_llm=True,
                    error_categories=(),
                    severity=1.0,
                    context_scope=ContextScope.SENTENCE,
                    english_confidence=1.0,
                    backend="test",
                    elapsed_ms=1.0,
                ),
                plan=ExecutionPlan(
                    policy_version=1,
                    mode=PolishMode.BALANCED,
                    task=value.task,
                    use_llm=True,
                    model="gemma4:12b",
                    backend=RuntimeBackend.CUDA,
                ),
                validation=ValidationResult(
                    proofread,
                    1.0,
                    () if proofread else ("protected facts or tokens changed",),
                ),
                used_llm=True,
                rejected_llm=not proofread,
                quality_complete=proofread,
                tone_verified=proofread,
                quality_score=1.0 if proofread else 0.0,
                review_ready=proofread,
            )

    adapter = PolishWritingAssistantAdapter(
        RecordingEngine(),
        mode_provider=lambda: PolishMode.BALANCED,
        consent_provider=lambda: True,
    )

    result = adapter.run(
        "The final report are due October 12; it must preserve patient privacy.",
        command=command,
    )

    assert result.text == (
        "The final report is due October 12; it must preserve patient privacy."
    )
    assert adapter.last_correction_result.quality_complete
    assert adapter.last_correction_result.tone_verified
    assert adapter.last_correction_result.review_ready


def test_tone_adapter_keeps_ai_proofread_when_style_is_unsafe():
    class RecordingEngine:
        def __init__(self):
            self.requests = []

        def analyze(self, value):
            self.requests.append(value)
            proofread = value.command == "Proofread"
            output = (
                "The funding must stop unless the council approves it."
                if proofread
                else "The funding can continue without council approval."
            )
            return CorrectionResult(
                original_text=value.text,
                normalized_text=value.text,
                deterministic_text=value.text,
                text=output,
                classifier=ClassifierDecision(
                    needs_llm=True,
                    error_categories=(),
                    severity=1.0,
                    context_scope=ContextScope.SENTENCE,
                    english_confidence=1.0,
                    backend="test",
                    elapsed_ms=1.0,
                ),
                plan=ExecutionPlan(
                    policy_version=1,
                    mode=PolishMode.BALANCED,
                    task=value.task,
                    use_llm=True,
                    model="qwen3:8b",
                    backend=RuntimeBackend.CUDA,
                ),
                validation=ValidationResult(
                    proofread,
                    1.0,
                    () if proofread else ("negation changed",),
                ),
                used_llm=proofread,
                rejected_llm=not proofread,
                quality_complete=proofread,
                tone_verified=proofread,
                quality_score=1.0 if proofread else 0.0,
                generation_seconds=1.0,
                review_ready=proofread,
                residual_errors=() if proofread else ("negation changed",),
            )

    adapter = PolishWritingAssistantAdapter(
        RecordingEngine(),
        mode_provider=lambda: PolishMode.BALANCED,
        consent_provider=lambda: True,
    )

    result = adapter.run(
        "The funding must stops unless council approve it.",
        command="Friendly",
    )

    assert result.text == (
        "The funding must stop unless the council approves it."
    )
    correction = adapter.last_correction_result
    assert correction.review_ready
    assert correction.used_llm
    assert correction.rejected_llm
    assert correction.quality_complete
    assert not correction.tone_verified
    assert correction.quality_score == 1.0
    assert "Correction ready" in correction.message
    assert "Friendly style was not safely applied" in correction.message
    assert "Friendly tone was not verified" in correction.residual_errors


def test_persuasive_adapter_keeps_verified_ai_proofread_role_after_unsafe_style():
    class RecordingEngine:
        def analyze(self, value):
            proofread = value.command == "Proofread"
            output = (
                "The proposal should proceed because the survey shows demand."
                if proofread
                else "The proposal will proceed, and everybody supports it."
            )
            return CorrectionResult(
                original_text=value.text,
                normalized_text=value.text,
                deterministic_text=value.text,
                text=output,
                classifier=ClassifierDecision(
                    needs_llm=True,
                    error_categories=(),
                    severity=1.0,
                    context_scope=ContextScope.SENTENCE,
                    english_confidence=1.0,
                    backend="test",
                    elapsed_ms=1.0,
                ),
                plan=ExecutionPlan(
                    policy_version=1,
                    mode=PolishMode.BALANCED,
                    task=value.task,
                    use_llm=True,
                    model="qwen3:8b",
                    backend=RuntimeBackend.CUDA,
                ),
                validation=ValidationResult(
                    proofread,
                    1.0,
                    () if proofread else ("invented actor",),
                ),
                used_llm=proofread,
                rejected_llm=not proofread,
                quality_complete=proofread,
                tone_verified=proofread,
                quality_score=1.0 if proofread else 0.0,
                generation_seconds=1.0,
                review_ready=proofread,
            )

    adapter = PolishWritingAssistantAdapter(
        RecordingEngine(),
        mode_provider=lambda: PolishMode.BALANCED,
        consent_provider=lambda: True,
    )

    result = adapter.run(
        "The proposal should proceeds because the survey show demand.",
        command="Persuasive",
    )

    assert result.text == (
        "The proposal should proceed because the survey shows demand."
    )
    correction = adapter.last_correction_result
    assert correction.quality_complete
    assert correction.tone_verified
    assert correction.review_ready
    assert correction.rejected_llm
    assert correction.residual_errors == ()
    assert "Persuasive role already verified" in correction.message


def test_cooperative_adapter_repairs_clear_team_action_after_ai_style_failure():
    proofread_text = (
        "The Northstar Field Services team was supposed to launch DispatchGrid "
        "3.2 on Monday, November 16, but the final review did not occur as planned."
    )

    class RecordingEngine:
        def analyze(self, value):
            succeeded = value.command == "Proofread"
            return CorrectionResult(
                original_text=value.text,
                normalized_text=value.text,
                deterministic_text=value.text,
                text=proofread_text,
                classifier=ClassifierDecision(
                    needs_llm=True,
                    error_categories=(),
                    severity=1.0,
                    context_scope=ContextScope.SENTENCE,
                    english_confidence=1.0,
                    backend="test",
                    elapsed_ms=1.0,
                ),
                plan=ExecutionPlan(
                    policy_version=1,
                    mode=PolishMode.BALANCED,
                    task=value.task,
                    use_llm=True,
                    model="qwen3:8b",
                    backend=RuntimeBackend.CUDA,
                ),
                validation=ValidationResult(succeeded, 1.0, ()),
                used_llm=succeeded,
                rejected_llm=not succeeded,
                quality_complete=succeeded,
                tone_verified=succeeded,
                quality_score=1.0 if succeeded else 0.0,
                generation_seconds=1.0,
                review_ready=succeeded,
                residual_errors=(
                    ()
                    if succeeded
                    else ("cooperative rewrite is too close to the source",)
                ),
            )

    adapter = PolishWritingAssistantAdapter(
        RecordingEngine(),
        mode_provider=lambda: PolishMode.BALANCED,
        consent_provider=lambda: True,
    )

    result = adapter.run(
        "The Northstar Field Services team was suppose to launch DispatchGrid "
        "3.2 on Monday, November 16, but the final review didnt happen.",
        command="Cooperative",
    )

    assert result.text == (
        "The Northstar Field Services team was supposed to work together to "
        "launch DispatchGrid 3.2 on Monday, November 16, but the final review "
        "did not occur as planned."
    )
    correction = adapter.last_correction_result
    assert correction.quality_complete
    assert correction.tone_verified
    assert correction.review_ready
    assert correction.rejected_llm
    assert correction.residual_errors == ()


def test_caveman_adapter_uses_safe_ai_proofread_transform_without_second_generation():
    proofread_text = (
        "The draft uses the word failure for low attendance, but that is poor "
        "vocabulary because it blames residents."
    )

    class RecordingEngine:
        def __init__(self):
            self.requests = []

        def analyze(self, value):
            self.requests.append(value)
            succeeded = value.command == "Proofread"
            output = proofread_text if succeeded else value.text
            return CorrectionResult(
                original_text=value.text,
                normalized_text=value.text,
                deterministic_text=value.text,
                text=output,
                classifier=ClassifierDecision(
                    needs_llm=True,
                    error_categories=(),
                    severity=1.0,
                    context_scope=ContextScope.SENTENCE,
                    english_confidence=1.0,
                    backend="test",
                    elapsed_ms=1.0,
                ),
                plan=ExecutionPlan(
                    policy_version=1,
                    mode=PolishMode.BALANCED,
                    task=value.task,
                    use_llm=True,
                    model="qwen3:8b",
                    backend=RuntimeBackend.CUDA,
                ),
                validation=ValidationResult(
                    succeeded,
                    1.0,
                    () if succeeded else ("negation changed",),
                ),
                used_llm=succeeded,
                rejected_llm=not succeeded,
                quality_complete=succeeded,
                tone_verified=succeeded,
                quality_score=1.0 if succeeded else 0.0,
                generation_seconds=1.0,
                review_ready=succeeded,
            )

    engine_value = RecordingEngine()
    adapter = PolishWritingAssistantAdapter(
        engine_value,
        mode_provider=lambda: PolishMode.BALANCED,
        consent_provider=lambda: True,
    )

    result = adapter.run(
        "The draft use failure for low attendance but thats bad vocabulary.",
        command="Caveman",
    )

    assert [value.command for value in engine_value.requests] == ["Proofread"]
    assert result.text == (
        "The draft uses the word failure for low attendance. That is poor "
        "vocabulary because it blames residents."
    )
    assert adapter.last_correction_result.quality_complete
    assert adapter.last_correction_result.review_ready
    assert not adapter.last_correction_result.rejected_llm


def test_caveman_adapter_prefers_verified_proofread_over_unsafe_style_drift():
    proofread_text = (
        "The maintenance record does not show who completed the final load test, "
        "but the manager must keep the original record."
    )

    class RecordingEngine:
        def __init__(self):
            self.requests = []

        def analyze(self, value):
            self.requests.append(value)
            output = (
                proofread_text
                if value.command == "Proofread"
                else (
                    "The maintenance record don't shows who did the test. "
                    "The manager keeps the record without."
                )
            )
            return CorrectionResult(
                original_text=value.text,
                normalized_text=value.text,
                deterministic_text=value.text,
                text=output,
                classifier=ClassifierDecision(
                    needs_llm=True,
                    error_categories=(),
                    severity=1.0,
                    context_scope=ContextScope.SENTENCE,
                    english_confidence=1.0,
                    backend="test",
                    elapsed_ms=1.0,
                ),
                plan=ExecutionPlan(
                    policy_version=1,
                    mode=PolishMode.BALANCED,
                    task=value.task,
                    use_llm=True,
                    model="qwen3:8b",
                    backend=RuntimeBackend.CUDA,
                ),
                validation=ValidationResult(True, 1.0),
                used_llm=True,
                quality_complete=True,
                tone_verified=True,
                quality_score=1.0,
                generation_seconds=1.0,
                review_ready=True,
            )

    engine_value = RecordingEngine()
    adapter = PolishWritingAssistantAdapter(
        engine_value,
        mode_provider=lambda: PolishMode.BALANCED,
        consent_provider=lambda: True,
    )

    result = adapter.run(
        "The maintenance record dont show who completed the final load test, "
        "but the manager must keep the original record.",
        command="Caveman",
    )

    assert result.text == (
        "The maintenance record does not show who completed the final load test. "
        "The manager must keep the original record."
    )
    assert adapter.last_correction_result.quality_complete
    assert adapter.last_correction_result.review_ready
    assert not adapter.last_correction_result.rejected_llm


def test_caveman_adapter_prefers_safe_ai_proofread_transform_over_tone_rewrite():
    proofread_text = (
        "The maintenance record does not show who completed the test, "
        "but the manager must keep the original record."
    )
    caveman_text = (
        "The record does not show who completed the test. "
        "The manager must keep the original."
    )

    class RecordingEngine:
        def __init__(self):
            self.requests = []

        def analyze(self, value):
            self.requests.append(value)
            output = proofread_text if value.command == "Proofread" else caveman_text
            return CorrectionResult(
                original_text=value.text,
                normalized_text=value.text,
                deterministic_text=value.text,
                text=output,
                classifier=ClassifierDecision(
                    needs_llm=True,
                    error_categories=(),
                    severity=1.0,
                    context_scope=ContextScope.SENTENCE,
                    english_confidence=1.0,
                    backend="test",
                    elapsed_ms=1.0,
                ),
                plan=ExecutionPlan(
                    policy_version=1,
                    mode=PolishMode.BALANCED,
                    task=value.task,
                    use_llm=True,
                    model="qwen3:8b",
                    backend=RuntimeBackend.CUDA,
                ),
                validation=ValidationResult(True, 1.0),
                used_llm=True,
                quality_complete=True,
                tone_verified=True,
                quality_score=1.0,
                generation_seconds=1.0,
                review_ready=True,
            )

    adapter = PolishWritingAssistantAdapter(
        RecordingEngine(),
        mode_provider=lambda: PolishMode.BALANCED,
        consent_provider=lambda: True,
    )

    result = adapter.run(
        "The maintenance record dont show who completed the test, "
        "but the manager must keep the original record.",
        command="Caveman",
    )

    assert result.text == (
        "The maintenance record does not show who completed the test. "
        "The manager must keep the original record."
    )
    assert adapter.last_correction_result.quality_complete
    assert not adapter.last_correction_result.rejected_llm


class MemoryStore:
    def __init__(self):
        self.values = {}

    def get_meta(self, key, default=None):
        return self.values.get(key, default)

    def set_meta(self, key, value):
        self.values[key] = value


class FakeRules:
    def warm(self):
        return True

    def correct(self, text, *, user_terms=()):
        corrected = text.replace("Teh ", "The ")
        return DeterministicPolishResult(
            corrected,
            (),
            (),
            1.0,
        )


class FakeClassifier:
    def __init__(
        self,
        *,
        needs_llm=True,
        similarity=0.99,
        english_confidence=1.0,
    ):
        self.needs_llm = needs_llm
        self.similarity = similarity
        self.english_confidence = english_confidence

    def warm(self):
        return True

    def classify(self, source, deterministic, *, task, deterministic_categories=()):
        return ClassifierDecision(
            needs_llm=self.needs_llm,
            error_categories=("agreement",) if self.needs_llm else deterministic_categories,
            severity=0.95 if self.needs_llm else 0.1,
            context_scope=ContextScope.SENTENCE,
            english_confidence=self.english_confidence,
            backend="fake",
            elapsed_ms=1.0,
        )

    def semantic_similarity(self, source, candidate):
        return self.similarity


class FakePacks:
    def __init__(self, pack):
        self.pack = pack
        self.ensure_calls = 0

    def catalog(self, *, refresh_installed=True):
        return (self.pack,)

    def ensure(self, plan, *, consent, progress=None):
        self.ensure_calls += 1
        return self.pack


class FakeRuntime:
    runtime_id = "fake:1"

    def __init__(self, outputs=(), *, probe=True, error=""):
        self.outputs = list(outputs)
        self.probe = probe
        self.error = error
        self.generate_calls = []
        self.probe_calls = 0

    def generate(self, request):
        self.generate_calls.append(request)
        if self.error:
            raise RuntimeError(self.error)
        value = self.outputs.pop(0)
        if isinstance(value, BaseException):
            raise value
        return GenerationResult(
            value,
            request.plan.model,
            RuntimeBackend.CUDA,
            0.25,
        )

    def status(self, model, *, timeout=1.0):
        return RuntimeStatus(model, True, RuntimeBackend.CUDA, 9 * GIB, 9 * GIB)

    def acceptance_probe(self, plan):
        self.probe_calls += 1
        return AcceptanceProbeResult(
            accepted=self.probe,
            elapsed_seconds=0.5,
            correctness_passed=self.probe,
            protected_passed=self.probe,
            backend=RuntimeBackend.CUDA,
            reason="" if self.probe else "probe failed",
        )


def hardware():
    return HardwareSnapshot(
        cpu_name="CPU",
        cpu_threads=16,
        cpu_features=("avx2",),
        total_ram_bytes=64 * GIB,
        available_ram_bytes=48 * GIB,
        gpu_name="GPU",
        gpu_vendor="NVIDIA",
        dedicated_vram_bytes=16 * GIB,
        available_vram_bytes=16 * GIB,
        driver_version="1",
        driver_healthy=True,
        supported_backends=(RuntimeBackend.CUDA, RuntimeBackend.CPU),
        runtime_version="fake",
    )


def model_pack():
    return PolishModelPack(
        pack_id="gemma4-e4b-qat-v1",
        display_name="Gemma 4 E4B QAT",
        model="gemma4:e4b-it-qat",
        family="Gemma 4",
        parameters="4.5B effective",
        quantization="Q4_0",
        runtime="ollama",
        manifest_digest="a" * 64,
        size_bytes=6 * GIB,
        min_ram_bytes=16 * GIB,
        min_vram_bytes=8 * GIB,
        max_context_tokens=4096,
        supported_backends=(RuntimeBackend.CUDA,),
        license_id="Apache-2.0",
        source_url="https://example.test",
        download_ref="ollama://gemma4:e4b-it-qat",
        installed=True,
        validated=True,
    )


def probe_plan(
    pack,
    *,
    mode=PolishMode.BALANCED,
    backend=RuntimeBackend.CUDA,
    require_full_gpu=True,
):
    latency = 15.0 if mode is PolishMode.PROFESSIONAL else 5.0
    if mode is PolishMode.INSTANT:
        latency = 2.0
    return ExecutionPlan(
        policy_version=6,
        mode=mode,
        task=PolishTask.PROOFREAD,
        use_llm=True,
        pack_id=pack.pack_id,
        model=pack.model,
        backend=backend,
        require_full_gpu=require_full_gpu,
        warm_latency_limit_seconds=latency,
    )


def engine(*, classifier, runtime, store=None, rules=None):
    store = store or MemoryStore()
    pack = model_pack()
    return (
        PolishEngine(
            rules=rules or FakeRules(),
            classifier=classifier,
            packs=FakePacks(pack),
            runtime=runtime,
            hardware_provider=hardware,
            benchmark_cache=PolishBenchmarkCache(store),
        ),
        store,
    )


def request(text, *, mode=PolishMode.BALANCED):
    return CorrectionRequest(
        text,
        task=PolishTask.PROOFREAD,
        mode=mode,
        command="Proofread",
        manual=True,
    )


def test_manual_proofread_always_calls_runtime_even_when_classifier_says_clean():
    runtime = FakeRuntime(("The report is ready.",))
    value, _ = engine(classifier=FakeClassifier(needs_llm=False), runtime=runtime)
    result = value.analyze(request("Teh report is ready"))
    assert result.text == "The report is ready."
    assert result.used_llm
    assert len(runtime.generate_calls) == 1
    assert "polished rewrite" in runtime.generate_calls[0].instruction.casefold()
    assert "final verification pass" in runtime.generate_calls[0].instruction.casefold()
    assert "comma splices" in runtime.generate_calls[0].instruction.casefold()


def test_proofread_accepts_case_corrected_existing_product_name():
    source = "provide the google play consol link to insall the app"
    corrected = "Provide the Google Play console link to install the app."
    runtime = FakeRuntime((corrected, corrected, corrected))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(request(source))

    assert result.text == corrected
    assert result.quality_complete
    assert result.review_ready
    assert result.validation.accepted


def test_proofread_accepts_typo_corrected_existing_product_name():
    source = "provide the google play consol link to insall the app"
    corrected = "Provide the Google Play Console link to install the app."
    runtime = FakeRuntime((corrected, corrected, corrected))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(request(source))

    assert result.text == corrected
    assert result.quality_complete
    assert result.review_ready
    assert result.validation.accepted


def test_persuasive_near_copy_is_retried_until_style_is_visible():
    source = (
        "The inspection found missed bolts. Those bolts can cause panel "
        "movement."
    )
    persuasive = (
        "The inspection found missed bolts. This evidence shows that those "
        "bolts can cause panel movement."
    )
    runtime = FakeRuntime((source, persuasive))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.TONE,
            mode=PolishMode.BALANCED,
            command="Persuasive",
            manual=True,
        )
    )

    assert result.text == persuasive
    assert result.quality_complete
    assert result.tone_verified
    assert len(runtime.generate_calls) == 2
    assert "persuasive rewrite is too close" in (
        runtime.generate_calls[1].instruction.casefold()
    )
    assert "do not copy the prior wording" in (
        runtime.generate_calls[1].instruction.casefold()
    )
    assert "smallest safe correction" not in (
        runtime.generate_calls[1].instruction.casefold()
    )





def test_concise_accepts_safe_segment_recovery_after_ai_near_copy():
    source = (
        "There are also several punctuation and structure problems in the preliminary report. "
        "One paragraph lists soil testing, tool storage, compost training, and language access "
        "without commas. Another paragraph repeats the same conclusion three times in slightly "
        "different words. The methods section jumps from recruitment to results and then back to "
        "consent procedures; readers get confused about the actual sequence."
    )
    unsafe = (
        "There are also several punctuation and structural issues in the preliminary report. "
        "One paragraph lists soil testing tool storage compost training and language access "
        "without commas. Another paragraph repeats the same conclusion three times in slightly "
        "different words. The methods section jumps from recruitment to results and then back to "
        "consent procedures; readers get confused about the actual sequence."
    )
    runtime = FakeRuntime((unsafe, unsafe))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.REWRITE,
            mode=PolishMode.BALANCED,
            command="Concise",
            manual=True,
        )
    )

    assert not result.quality_complete
    assert result.tone_verified
    assert not result.used_llm
    assert not result.review_ready
    assert "local ai" not in result.message.casefold()
    assert "The preliminary report has several punctuation and structural problems." in result.text
    assert "soil testing, tool storage, compost training, and language access" in result.text
    assert "repeats the conclusion" in result.text
    assert "the sequence" in result.text


def test_all_deterministic_concise_segment_recovery_keeps_ai_provenance_false(
    monkeypatch,
):
    source = "First sentence. Second sentence."
    runtime = FakeRuntime(
        (
            RuntimeError("changed protected content"),
            RuntimeError("changed protected content"),
        )
    )
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)
    whole_calls = 0

    def quality(source_text, candidate, *, command):
        nonlocal whole_calls
        if source_text == source and candidate == source:
            whole_calls += 1
            if whole_calls == 1:
                return SimpleNamespace(
                    complete=False,
                    residual_errors=("structure",),
                    tone_verified=True,
                    score=0.8,
                )
        return SimpleNamespace(
            complete=True,
            residual_errors=(),
            tone_verified=True,
            score=1.0,
        )

    value._evaluate_quality = quality
    monkeypatch.setattr(
        "echo_link_core.polish._validate_rephrase_actions",
        lambda validation, *_args, **_kwargs: validation,
    )
    recovery, calls, _, _ = value._recover_failed_generation(
        request=CorrectionRequest(
            source,
            task=PolishTask.REWRITE,
            mode=PolishMode.BALANCED,
            command="Concise",
            manual=True,
        ),
        normalized=source,
        deterministic=source,
        plan=probe_plan(model_pack()),
        instruction="",
        safe_candidates=[],
    )

    assert calls == 2
    assert recovery is not None
    assert recovery.text == source
    assert not recovery.used_llm
def test_concise_safely_shortens_interview_access_wording():
    source = (
        "Before any interview, participants received form LU-CG-12 and could decline "
        "audio recording without loosing access to a garden plot. No recording was "
        "shared outside the encrypted project drive. The retention plan requires "
        "deletion on August 31, 2026, unless the university ethics board approves a "
        "written extension."
    )
    corrected = source.replace("loosing", "losing")
    runtime = FakeRuntime((corrected, corrected))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.REWRITE,
            mode=PolishMode.BALANCED,
            command="Concise",
            manual=True,
        )
    )

    assert result.quality_complete
    assert result.tone_verified
    assert result.text.startswith("Before interviews,")
    assert "without losing garden plot access" in result.text
    assert "LU-CG-12" in result.text
    assert "August 31, 2026" in result.text

def test_concise_recovers_correct_ai_near_copy_before_sentence_split():
    source = (
        "These numbers sounds impressive but quantity by itself don't explain who had "
        "access to plots or why some residents stopped participating. Because the survey "
        "was only availible in English for the first six weeks. Which caused fewer "
        "responses from households that primarily spoke Spanish or Vietnamese."
    )
    corrected = (
        "These numbers sound impressive, but quantity by itself does not explain who had "
        "access to plots or why some residents stopped participating. The survey was only "
        "available in English for the first six weeks, which caused fewer responses from "
        "households that primarily spoke Spanish or Vietnamese."
    )
    runtime = FakeRuntime((corrected, corrected))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.REWRITE,
            mode=PolishMode.BALANCED,
            command="Concise",
            manual=True,
        )
    )

    assert result.quality_complete
    assert result.tone_verified
    assert "quantity alone" in result.text
    assert "Spanish- or Vietnamese-speaking households" in result.text

def test_concise_command_restores_fake_entity_casing_for_staged_proofread_task():
    source = (
        "Community gardens supposedly solve every neighborhood problem, but that "
        "repeated claim is too simple and overly broad."
    )
    concise = (
        "Community Gardens supposedly solve every neighborhood problem, but that "
        "claim is simplistic and broad."
    )
    runtime = FakeRuntime((concise,))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.PROOFREAD,
            mode=PolishMode.BALANCED,
            command="Concise",
            manual=True,
        )
    )

    assert result.text.startswith("Community gardens supposedly")
    assert result.quality_complete
    assert result.tone_verified
def test_concise_near_copy_is_retried_until_word_count_is_reduced():
    source = (
        "The team will carefully review the incident report and then provide a "
        "written response before the meeting begins."
    )
    concise = (
        "The team will review the incident report and provide a written response "
        "before the meeting."
    )
    runtime = FakeRuntime((source, concise))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.TONE,
            mode=PolishMode.BALANCED,
            command="Concise",
            manual=True,
        )
    )

    assert result.text == concise
    assert result.quality_complete
    assert result.tone_verified
    assert len(runtime.generate_calls) == 2
    assert "concise rewrite did not reduce word count" in (
        runtime.generate_calls[1].instruction.casefold()
    )
    assert "use no more than 17 words" in (
        runtime.generate_calls[1].instruction.casefold()
    )
def test_cooperative_safe_fallback_keeps_ai_provenance_false():
    source = "The team must compare the logs and report failures."
    cooperative = (
        "The team must work together to compare the logs and report failures."
    )
    runtime = FakeRuntime((source,))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.TONE,
            mode=PolishMode.BALANCED,
            command="Cooperative",
            manual=True,
        )
    )

    assert result.text == cooperative
    assert not result.quality_complete
    assert result.tone_verified
    assert not result.used_llm
    assert result.rejected_llm
    assert not result.review_ready
    assert len(runtime.generate_calls) == 1
    assert "not ready for review" in result.message
    assert "local ai plus" not in result.message.casefold()


def test_assertive_safe_fallback_keeps_ai_provenance_false():
    source = (
        "The researchers corrected the problem by adding translated forms and "
        "hiring Linh Tran and Mateo Silva."
    )
    unsafe = (
        "The researchers resolved the problem by adding translated forms and "
        "engaging Linh Tran and Mateo Silva."
    )
    runtime = FakeRuntime((unsafe,))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.TONE,
            mode=PolishMode.BALANCED,
            command="Assertive",
            manual=True,
        )
    )

    assert result.text == source
    assert not result.quality_complete
    assert result.tone_verified
    assert not result.used_llm
    assert result.rejected_llm
    assert not result.review_ready
    assert len(runtime.generate_calls) == 1
    assert "not ready for review" in result.message
    assert "local ai plus" not in result.message.casefold()


def test_cooperative_does_not_invent_collaboration_for_participant_counts():
    source = "The drill includes 72 volunteers and 18 staff members."
    runtime = FakeRuntime((source,))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.TONE,
            mode=PolishMode.BALANCED,
            command="Cooperative",
            manual=True,
        )
    )

    assert result.text == source
    assert result.quality_complete
    assert result.tone_verified
    assert len(runtime.generate_calls) == 1


def test_cooperative_does_not_invent_collaboration_for_single_team_leader():
    source = "At each stage, a team leader records the time."
    runtime = FakeRuntime((source,))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.TONE,
            mode=PolishMode.BALANCED,
            command="Cooperative",
            manual=True,
        )
    )

    assert result.text == source
    assert result.quality_complete
    assert result.tone_verified
    assert len(runtime.generate_calls) == 1


def test_cooperative_does_not_invent_collaboration_for_team_recipient():
    source = "The plan assigns radio channel 6 to medical teams."
    runtime = FakeRuntime((source,))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.TONE,
            mode=PolishMode.BALANCED,
            command="Cooperative",
            manual=True,
        )
    )

    assert result.text == source
    assert result.quality_complete
    assert result.tone_verified
    assert len(runtime.generate_calls) == 1


def test_safe_cooperative_signal_handles_compound_team_past_action():
    source = "The support team marked the issue as cosmetic."
    candidate = _apply_safe_cooperative_signal(source)
    assert candidate == (
        "The support team worked together to mark the issue as cosmetic."
    )
    value, _ = engine(classifier=FakeClassifier(), runtime=FakeRuntime(()))
    quality = value._evaluate_quality(
        source,
        candidate,
        command="Cooperative",
    )
    assert quality.complete
    assert quality.tone_verified


def test_cooperative_does_not_invent_collaboration_for_single_worker_role():
    source = "The trainer must explain the route."
    assert _apply_safe_cooperative_signal(source) == source
    runtime = FakeRuntime((source,))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.TONE,
            mode=PolishMode.BALANCED,
            command="Cooperative",
            manual=True,
        )
    )

    assert result.text == source
    assert result.quality_complete
    assert result.tone_verified
    assert len(runtime.generate_calls) == 1


def test_safe_cooperative_signal_preserves_compound_actions_and_hiring():
    source = (
        "The researchers corrected the problem by adding translated forms and "
        "hiring Linh Tran and Mateo Silva."
    )
    candidate = _apply_safe_cooperative_signal(source)

    assert candidate == (
        "The researchers worked together to correct the problem by adding "
        "translated forms and hiring Linh Tran and Mateo Silva."
    )
    assert "hiring Linh Tran and Mateo Silva" in candidate
    value, _ = engine(classifier=FakeClassifier(), runtime=FakeRuntime(()))
    validation = value.validator.validate(
        source,
        candidate,
        task=PolishTask.TONE,
        grammar_mode=False,
    )
    quality = value._evaluate_quality(
        source,
        candidate,
        command="Cooperative",
    )
    assert validation.accepted, validation.failures
    assert quality.complete
    assert quality.tone_verified


def test_cooperative_replaces_dropped_hiring_with_safe_preserving_fallback():
    source = (
        "The researchers corrected the problem by adding translated forms and "
        "hiring Linh Tran and Mateo Silva."
    )
    dropped_hiring = (
        "The researchers addressed the problem by adding translated forms and "
        "working together with Linh Tran and Mateo Silva."
    )
    runtime = FakeRuntime((dropped_hiring,))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.TONE,
            mode=PolishMode.BALANCED,
            command="Cooperative",
            manual=True,
        )
    )

    assert result.text == (
        "The researchers worked together to correct the problem by adding "
        "translated forms and hiring Linh Tran and Mateo Silva."
    )
    assert result.text != dropped_hiring
    assert not result.quality_complete
    assert result.tone_verified
    assert not result.used_llm
    assert result.rejected_llm
    assert not result.review_ready
    assert len(runtime.generate_calls) == 1


def test_already_persuasive_reason_does_not_require_empty_padding():
    source = (
        "The library should test later hours because students need a quiet "
        "public space after 7:00 p.m."
    )
    runtime = FakeRuntime((source,))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.TONE,
            mode=PolishMode.BALANCED,
            command="Persuasive",
            manual=True,
        )
    )

    assert result.text == source
    assert result.quality_complete
    assert result.tone_verified
    assert len(runtime.generate_calls) == 1


def test_persuasive_requirement_chunk_does_not_require_invented_reason():
    source = "Council must approve the limited trial before April 6."
    runtime = FakeRuntime((source,))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.TONE,
            mode=PolishMode.BALANCED,
            command="Persuasive",
            manual=True,
        )
    )

    assert result.text == source
    assert result.quality_complete
    assert result.tone_verified
    assert len(runtime.generate_calls) == 1


def test_persuasive_quantified_evidence_chunk_is_valid_support():
    source = (
        "The group recorded 4,860 volunteer hours and 7,240 pounds of "
        "produce."
    )
    runtime = FakeRuntime((source,))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.TONE,
            mode=PolishMode.BALANCED,
            command="Persuasive",
            manual=True,
        )
    )

    assert result.quality_complete
    assert result.tone_verified
    assert len(runtime.generate_calls) == 1


def test_persuasive_access_limitation_chunk_is_valid_support():
    source = (
        "The survey was available only in English, which caused fewer "
        "responses from some households."
    )
    runtime = FakeRuntime((source,))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.TONE,
            mode=PolishMode.BALANCED,
            command="Persuasive",
            manual=True,
        )
    )

    assert result.quality_complete
    assert result.tone_verified
    assert len(runtime.generate_calls) == 1


def test_persuasive_timed_action_chunk_is_valid_support():
    source = (
        "Ravi paused producers at 03:22 UTC. Sofia restarted workers at "
        "03:29 UTC."
    )
    runtime = FakeRuntime((source,))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.TONE,
            mode=PolishMode.BALANCED,
            command="Persuasive",
            manual=True,
        )
    )

    assert result.quality_complete
    assert result.tone_verified
    assert len(runtime.generate_calls) == 1


def test_persuasive_contrast_restructure_is_visible_without_invented_claim():
    source = (
        "The exercise tests essential services, but several plans contain "
        "conflicting instructions."
    )
    persuasive = (
        "The exercise tests essential services. However, several plans "
        "contain conflicting instructions."
    )
    runtime = FakeRuntime((persuasive,))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.TONE,
            mode=PolishMode.BALANCED,
            command="Persuasive",
            manual=True,
        )
    )

    assert result.text == persuasive
    assert result.quality_complete
    assert result.tone_verified
    assert len(runtime.generate_calls) == 1


def test_recovery_sentence_splitter_does_not_treat_west_as_street_abbreviation():
    from echo_link_core.polish import _sentence_spans_for_recovery

    source = "Produce went to Community Pantry West. These numbers matter."
    spans = _sentence_spans_for_recovery(source)

    assert [source[start:end] for start, end in spans] == [
        "Produce went to Community Pantry West.",
        "These numbers matter.",
    ]


def test_manual_proofread_calls_ai_when_language_confidence_is_low():
    runtime = FakeRuntime(("The report is ready.",))
    value, _ = engine(
        classifier=FakeClassifier(english_confidence=0.2),
        runtime=runtime,
    )

    result = value.analyze(request("Teh report is ready."))

    assert result.text == "The report is ready."
    assert result.used_llm
    assert result.review_ready
    assert len(runtime.generate_calls) == 1


def test_safe_ai_output_is_returned_after_local_probe():
    runtime = FakeRuntime(("The reports are not ready, and it fails.",))
    value, store = engine(classifier=FakeClassifier(), runtime=runtime)
    result = value.analyze(request("The reports is not ready, and it fail."))
    assert result.text == "The reports are not ready, and it fails."
    assert result.used_llm
    assert runtime.probe_calls == 1
    assert result.plan.backend is RuntimeBackend.CUDA
    assert "polish_benchmark_cache" in store.values


def test_active_document_reprobes_when_driver_identity_changes():
    runtime = FakeRuntime(
        (
            "The reports are not ready, and it fails.",
            "The reports are not ready, and it fails.",
        )
    )
    store = MemoryStore()
    snapshots = iter(
        (
            hardware(),
            replace(hardware(), driver_version="1-transient"),
        )
    )
    value = PolishEngine(
        rules=FakeRules(),
        classifier=FakeClassifier(),
        packs=FakePacks(model_pack()),
        runtime=runtime,
        hardware_provider=lambda: next(snapshots),
        benchmark_cache=PolishBenchmarkCache(store),
    )

    first = value.analyze(request("The reports is not ready, and it fail."))
    second = value.analyze(request("The reports is not ready, and it fail."))

    assert first.quality_complete
    assert second.quality_complete
    assert runtime.probe_calls == 2


def test_tone_generation_protects_negation_surface_during_rewrite():
    source = (
        "These numbers sound impressive, but quantity alone does not explain access."
    )
    corrected = "These numbers sound good, but quantity alone does not explain access."
    runtime = FakeRuntime((corrected,) * 10)
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.TONE,
            mode=PolishMode.BALANCED,
            command="Caveman",
            manual=True,
        )
    )

    assert result.validation.accepted
    assert "does not explain" in runtime.generate_calls[0].protected_terms


def test_rephrase_generation_protects_negated_action_phrase():
    source = (
        "Approval of the trial does not authorize permanent hours after June 26."
    )
    runtime = FakeRuntime((source,) * 10)
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.REWRITE,
            mode=PolishMode.BALANCED,
            command="Rephrase",
            manual=True,
        )
    )

    assert result.validation.accepted, result.validation.failures
    assert "does not authorize" in runtime.generate_calls[0].protected_terms


def test_rephrase_generation_protects_complete_negated_claim():
    source = (
        "It should not claim gardens eliminate food insecurity, prevent crime, "
        "or guarantee community unity."
    )
    runtime = FakeRuntime((source,) * 10)
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.REWRITE,
            mode=PolishMode.BALANCED,
            command="Rephrase",
            manual=True,
        )
    )

    assert result.validation.accepted, result.validation.failures
    assert source.rstrip(".") in runtime.generate_calls[0].protected_terms


def test_proofread_does_not_lock_a_negation_typo_as_protected_text():
    class CorrectNegationRules(FakeRules):
        def correct(self, text, *, user_terms=()):
            corrected = text.replace(
                "That conclusion dont follow",
                "That conclusion doesn't follow",
            )
            return DeterministicPolishResult(corrected, (), (), 1.0)

    source = "That conclusion dont follow from the evidence."
    corrected = "That conclusion doesn't follow from the evidence."
    runtime = FakeRuntime((corrected,) * 10)
    value, _ = engine(
        classifier=FakeClassifier(),
        runtime=runtime,
        rules=CorrectNegationRules(),
    )

    result = value.analyze(request(source))

    assert result.validation.accepted
    assert "doesn't" not in runtime.generate_calls[0].protected_terms
    assert "dont" not in runtime.generate_calls[0].protected_terms


def test_tone_rewrite_repeats_plural_subject_instead_of_adding_pronoun():
    source = (
        "The researchers corrected the problem by adding translated forms and "
        "hiring Linh Tran and Mateo Silva."
    )
    generated = (
        "The researchers corrected the problem. They added translated forms. "
        "They hired Linh Tran and Mateo Silva."
    )
    runtime = FakeRuntime((generated,) * 10)
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.TONE,
            mode=PolishMode.BALANCED,
            command="Caveman",
            manual=True,
        )
    )

    assert result.text == (
        "The researchers corrected the problem. The researchers added translated "
        "forms. The researchers hired Linh Tran and Mateo Silva."
    )
    assert result.validation.accepted


def test_tone_rewrite_allows_existing_word_capitalized_at_new_sentence_start():
    source = "The report says stable leadership matters."
    generated = "Stable leadership matters. The report says this."
    runtime = FakeRuntime((generated,) * 10)
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.TONE,
            mode=PolishMode.BALANCED,
            command="Caveman",
            manual=True,
        )
    )

    assert result.text == generated
    assert result.validation.accepted


def test_tone_rewrite_allows_quotes_around_an_existing_word():
    source = "The draft uses the word failure for low attendance."
    generated = 'The draft uses the word "failure" for low attendance.'
    runtime = FakeRuntime((generated,) * 10)
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.TONE,
            mode=PolishMode.BALANCED,
            command="Caveman",
            manual=True,
        )
    )

    assert result.text == generated
    assert result.validation.accepted


def test_rephrase_restores_source_content_noun_before_similarity_gate():
    class AnchorClassifier(FakeClassifier):
        def semantic_similarity(self, source, candidate):
            return 0.87 if "simulation" in candidate.casefold() else 0.93

    source = "The response team is preparing a power outage drill."
    generated = "The response team is organizing a power outage simulation."
    runtime = FakeRuntime((generated,))
    value, _ = engine(classifier=AnchorClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.TONE,
            mode=PolishMode.BALANCED,
            command="Rephrase",
            manual=True,
        )
    )

    assert result.text == "The response team is organizing a power outage drill."
    assert result.validation.accepted
    assert result.quality_complete


def test_rephrase_retry_protects_source_content_words_lost_by_first_draft():
    class LostContentClassifier(FakeClassifier):
        def semantic_similarity(self, source, candidate):
            return 0.87 if "personnel" in candidate.casefold() else 0.99

    source = "The drill includes medical staff."
    runtime = FakeRuntime(
        (
            "They feature personnel.",
            "Medical staff are included in the drill.",
        )
    )
    value, _ = engine(classifier=LostContentClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.TONE,
            mode=PolishMode.BALANCED,
            command="Rephrase",
            manual=True,
        )
    )

    assert result.validation.accepted, result.validation.failures
    assert result.quality_complete
    retry_terms = runtime.generate_calls[1].protected_terms
    assert "drill" in retry_terms
    assert "includes" in retry_terms
    assert "medical staff" in retry_terms


def test_rephrase_retries_when_source_action_verbs_are_replaced():
    source = "Department leads compare the logs and explain time gaps."
    runtime = FakeRuntime(
        (
            "Department heads review the logs and identify time gaps.",
            "The logs are compared by department leads, who explain time gaps.",
        )
    )
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.TONE,
            mode=PolishMode.BALANCED,
            command="Rephrase",
            manual=True,
        )
    )

    assert result.validation.accepted, result.validation.failures
    assert result.quality_complete
    retry_terms = runtime.generate_calls[1].protected_terms
    assert "explain" in retry_terms


def test_preverified_style_fallback_uses_one_generation_attempt():
    source = "The funding must stop unless the council approves it."
    runtime = FakeRuntime(("The funding can continue without council approval.",))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.TONE,
            mode=PolishMode.BALANCED,
            command="Friendly",
            manual=True,
            preverified_fallback_available=True,
        )
    )

    assert len(runtime.generate_calls) == 1
    assert not result.quality_complete


def test_tone_generation_protects_pronoun_modal_action_phrase():
    source = "The checklist says it should contain twelve items."
    runtime = FakeRuntime((source,))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.TONE,
            mode=PolishMode.BALANCED,
            command="Shorten",
            manual=True,
        )
    )

    assert result.validation.accepted, result.validation.failures
    assert "it should contain" in runtime.generate_calls[0].protected_terms


def test_style_generation_protects_whether_alternative_fact_clause():
    source = (
        "Technicians have not determined whether the cable, tablet port, "
        "or software cache is responsible."
    )
    runtime = FakeRuntime((source,))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.REWRITE,
            mode=PolishMode.BALANCED,
            command="Shorten",
            manual=True,
        )
    )

    assert result.validation.accepted, result.validation.failures
    assert (
        "whether the cable, tablet port, or software cache is responsible"
        in runtime.generate_calls[0].protected_terms
    )


def test_proofread_generation_protects_temporal_fact_phrases():
    source = (
        "The caption stopped after nineteen minutes. "
        "The corrected file is due by November 18."
    )
    runtime = FakeRuntime((source,))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.PROOFREAD,
            mode=PolishMode.BALANCED,
            command="Proofread",
            manual=True,
        )
    )

    assert result.validation.accepted, result.validation.failures
    assert (
        "after nineteen minutes"
        in runtime.generate_calls[0].protected_terms
    )
    assert "by November 18" in runtime.generate_calls[0].protected_terms


def test_rephrase_generation_protects_ordering_calendar_date_phrase():
    source = (
        "Approval does not authorize permanent hours after June 26."
    )
    runtime = FakeRuntime((source,) * 10)
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.REWRITE,
            mode=PolishMode.BALANCED,
            command="Rephrase",
            manual=True,
        )
    )

    assert result.validation.accepted, result.validation.failures
    assert "after June 26" in runtime.generate_calls[0].protected_terms


def test_tone_rewrite_keeps_explicit_actor_when_removing_possessive_pronoun():
    source = (
        "It sounds as though residents were responsible for barriers outside "
        "their control."
    )
    generated = "It wrongly blames residents for outside barriers."
    runtime = FakeRuntime((generated,) * 10)
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.TONE,
            mode=PolishMode.BALANCED,
            command="Caveman",
            manual=True,
        )
    )

    assert result.text == generated
    assert result.validation.accepted


def test_tone_rewrite_does_not_treat_better_as_a_new_name():
    source = "The wording is poor."
    generated = "Better wording is clear."
    runtime = FakeRuntime((generated,) * 10)
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.TONE,
            mode=PolishMode.BALANCED,
            command="Caveman",
            manual=True,
        )
    )

    assert result.text == generated
    assert result.validation.accepted


def test_runtime_error_on_retry_still_uses_sentence_recovery():
    source = "The report is clear. The wording is simple."
    runtime = FakeRuntime(
        (
            "The report is clear. They say the wording is simple.",
            RuntimeError("Local writing model changed a meaning constraint."),
            "The report is clear.",
            "The wording is simple.",
        )
    )
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.TONE,
            mode=PolishMode.BALANCED,
            command="Caveman",
            manual=True,
        )
    )

    assert result.text == source
    assert result.used_llm
    assert result.review_ready
    assert len(runtime.generate_calls) == 4


def test_sentence_recovery_refuses_mixed_ai_and_rules_provenance():
    source = "The report is clear. The wording is simple."
    runtime = FakeRuntime(
        (
            "They say the report is clear. The wording is simple.",
            "They say the report is clear. The wording is simple.",
            "The report reads clearly.",
            "They say the wording is simple.",
        )
    )
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.TONE,
            mode=PolishMode.BALANCED,
            command="Rephrase",
            manual=True,
        )
    )

    assert result.text == source
    assert not result.quality_complete
    assert not result.review_ready
    assert not result.used_llm


def test_initial_model_safety_error_uses_sentence_recovery():
    source = "The report is clear. The wording is simple."
    runtime = FakeRuntime(
        (
            RuntimeError("Local writing model changed a meaning constraint."),
            "The report is clear.",
            "The wording is simple.",
        )
    )
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            source,
            task=PolishTask.TONE,
            mode=PolishMode.BALANCED,
            command="Caveman",
            manual=True,
        )
    )

    assert result.text == source
    assert result.used_llm
    assert result.review_ready
    assert len(runtime.generate_calls) == 3


def test_validator_allows_run_on_split_and_double_negative_repair():
    source = (
        "Dogs is good pets but not every dog is the same which gets annoying "
        "but dogs dont mean nothing bad by it."
    )
    corrected = (
        "Dogs are good pets, but not every dog is the same, which gets annoying. "
        "Dogs don't mean any harm by it."
    )
    runtime = FakeRuntime((corrected, corrected, corrected))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(request(source))

    assert result.text == corrected
    assert result.validation.accepted


def test_validator_allows_multiple_spelling_repairs_in_a_title():
    source = "Dogs Makes Grate Petts in Bakersfield"
    corrected = "Dogs Make Great Pets in Bakersfield"
    value, _ = engine(classifier=FakeClassifier(), runtime=FakeRuntime(()))

    validation = value.validator.validate(
        source,
        corrected,
        task=PolishTask.PROOFREAD,
        grammar_mode=True,
    )

    assert validation.accepted


def test_validator_rejects_dropped_modal_and_organizer_condition():
    source = (
        "The conclusion should state that gardens can support local learning when "
        "organizers have reliable resources."
    )
    unsafe = (
        "The conclusion should state that gardens support local learning with "
        "reliable resources."
    )
    value, _ = engine(classifier=FakeClassifier(), runtime=FakeRuntime(()))

    validation = value.validator.validate(
        source,
        unsafe,
        task=PolishTask.REWRITE,
        grammar_mode=False,
    )

    # The label now names the modal that moved ("modal meaning changed
    # (missing can)"), so match the prefix rather than the whole string.
    assert any(
        failure.startswith("modal meaning changed")
        for failure in validation.failures
    )
    assert any(x.startswith("actor changed") for x in validation.failures)
    assert not validation.accepted


def test_validator_allows_correction_that_preserves_modal_and_organizer():
    source = (
        "The conclusion should says that gardens can supports local learning when "
        "organizers has reliable resources."
    )
    corrected = (
        "The conclusion should say that gardens can support local learning when "
        "organizers have reliable resources."
    )
    value, _ = engine(classifier=FakeClassifier(), runtime=FakeRuntime(()))

    validation = value.validator.validate(
        source,
        corrected,
        task=PolishTask.REWRITE,
        grammar_mode=False,
    )

    assert validation.accepted

def test_validator_treats_should_not_contraction_as_same_obligation():
    source = "The conclusion should not claim gardens prevent crime."
    concise = "The conclusion shouldn't claim gardens prevent crime."
    value, _ = engine(classifier=FakeClassifier(), runtime=FakeRuntime(()))

    validation = value.validator.validate(
        source,
        concise,
        task=PolishTask.REWRITE,
        grammar_mode=False,
    )

    assert validation.accepted

def test_validator_rejects_modal_moved_between_program_and_city():
    source = (
        "The program will be free to visitors. Visitors will not pay a fee, but the "
        "city still pays operating costs."
    )
    unsafe = (
        "The program is free to visitors. Visitors will not pay a fee, but the city "
        "will cover operating costs."
    )
    value, _ = engine(classifier=FakeClassifier(), runtime=FakeRuntime(()))

    validation = value.validator.validate(
        source,
        unsafe,
        task=PolishTask.PROOFREAD,
        grammar_mode=True,
    )

    assert "modal actor changed" in validation.failures
    assert not validation.accepted


def test_validator_rejects_removed_obligation():
    source = "Employees must submit the report before Friday."
    unsafe = "Employees submit the report before Friday."
    value, _ = engine(classifier=FakeClassifier(), runtime=FakeRuntime(()))

    validation = value.validator.validate(
        source,
        unsafe,
        task=PolishTask.PROOFREAD,
        grammar_mode=True,
    )

    assert any(x.startswith("obligation strength changed") for x in validation.failures)
    assert not validation.accepted


def test_validator_rejects_changed_ordering_relationship():
    source = "Technicians inspect the system before deployment."
    unsafe = "Technicians inspect the system after deployment."
    value, _ = engine(classifier=FakeClassifier(), runtime=FakeRuntime(()))

    validation = value.validator.validate(
        source,
        unsafe,
        task=PolishTask.PROOFREAD,
        grammar_mode=True,
    )

    assert any(
        x.startswith("date or ordering relationship changed")
        for x in validation.failures
    )
    assert not validation.accepted


def test_validator_rejects_changed_common_role_actor():
    source = "The doctor reviewed the results."
    unsafe = "The nurse reviewed the results."
    value, _ = engine(classifier=FakeClassifier(), runtime=FakeRuntime(()))

    validation = value.validator.validate(
        source,
        unsafe,
        task=PolishTask.PROOFREAD,
        grammar_mode=True,
    )

    assert any(x.startswith("actor changed") for x in validation.failures)
    assert not validation.accepted


@pytest.mark.parametrize(
    ("source", "unsafe", "failure"),
    (
        (
            "The team approved the request.",
            "The team rejected the request.",
            "approval state reversed",
        ),
        (
            "The medicine increased the pain.",
            "The medicine decreased the pain.",
            "quantity direction reversed",
        ),
        (
            "The door is open.",
            "The door is closed.",
            "open state reversed",
        ),
    ),
)
def test_validator_rejects_explicit_action_state_contradictions(
    source,
    unsafe,
    failure,
):
    value, _ = engine(classifier=FakeClassifier(), runtime=FakeRuntime(()))

    validation = value.validator.validate(
        source,
        unsafe,
        task=PolishTask.PROOFREAD,
        grammar_mode=True,
    )

    assert failure in validation.failures
    assert not validation.accepted


def test_validator_checks_semantics_after_first_encoder_window():
    class WindowAwareClassifier(FakeClassifier):
        def __init__(self):
            super().__init__()
            self.calls = []

        def semantic_similarity(self, source, candidate):
            self.calls.append((source, candidate))
            if len(source.split()) > 96:
                return 0.99
            if "oranges" in candidate:
                return 0.20
            return 0.99

    prefix = " ".join(
        f"Sentence {index} explains the same stable report."
        for index in range(45)
    )
    source = f"{prefix} The final summary discusses apples."
    unsafe = f"{prefix} The final summary discusses oranges."
    classifier = WindowAwareClassifier()
    value, _ = engine(classifier=classifier, runtime=FakeRuntime(()))

    validation = value.validator.validate(
        source,
        unsafe,
        task=PolishTask.PROOFREAD,
        grammar_mode=True,
    )

    assert any("semantic similarity window" in x for x in validation.failures)
    assert any("oranges" in candidate for _, candidate in classifier.calls[1:])
    assert not validation.accepted


def test_validator_rejects_a_dropped_prohibition():
    """The half of modal drift that still refuses a candidate."""
    source = (
        "The program will be free to visitors. Visitors will not pay a fee, but the "
        "city still pays operating costs."
    )
    unsafe = (
        "The program will be free to visitors. Visitors will pay a fee, but the "
        "city still pays operating costs."
    )
    value, _ = engine(classifier=FakeClassifier(), runtime=FakeRuntime(()))

    validation = value.validator.validate(
        source,
        unsafe,
        task=PolishTask.PROOFREAD,
        grammar_mode=True,
    )

    assert not validation.accepted


def test_validator_allows_a_while_duration_spelling_repair():
    source = "You can pet the dog for awhile before dinner."
    corrected = "You can pet the dog for a while before dinner."
    runtime = FakeRuntime((corrected, corrected))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(request(source))

    assert result.text == corrected
    assert result.validation.accepted


def test_validator_allows_while_making_fragment_to_join_prior_sentence():
    source = (
        "The measures reduce known risks. "
        "While making clear that procedures remain unchanged."
    )
    corrected = (
        "The measures reduce known risks, while making clear that procedures "
        "remain unchanged."
    )
    value, _ = engine(classifier=FakeClassifier(), runtime=FakeRuntime(()))

    validation = value.validator.validate(
        source,
        corrected,
        task=PolishTask.PROOFREAD,
        grammar_mode=True,
    )

    assert validation.accepted


def test_validator_allows_role_case_change_when_while_fragment_is_joined():
    source = (
        "The cache stayed stale. While waiting for the refresh. "
        "Analyst Jonah Price checked the queue."
    )
    corrected = (
        "The cache stayed stale. While waiting for the refresh, "
        "analyst Jonah Price checked the queue."
    )
    value, _ = engine(classifier=FakeClassifier(), runtime=FakeRuntime(()))

    validation = value.validator.validate(
        source,
        corrected,
        task=PolishTask.PROOFREAD,
        grammar_mode=True,
    )

    assert validation.accepted


def test_validator_allows_planned_idiom_to_drop_non_actor_it():
    source = "The review did not happen as the plan said it would."
    corrected = "The review did not occur as planned."
    value, _ = engine(classifier=FakeClassifier(), runtime=FakeRuntime(()))

    validation = value.validator.validate(
        source,
        corrected,
        task=PolishTask.PROOFREAD,
        grammar_mode=True,
    )

    assert validation.accepted


def test_validator_allows_colloquial_planned_idiom_cleanup():
    source = "The review didn't happen like the plan said it would."
    corrected = "The review did not happen as planned."
    value, _ = engine(classifier=FakeClassifier(), runtime=FakeRuntime(()))

    validation = value.validator.validate(
        source,
        corrected,
        task=PolishTask.PROOFREAD,
        grammar_mode=True,
    )

    assert validation.accepted


def test_validator_allows_first_alert_pronoun_resolution():
    source = (
        "The first alert occurred at 02:17 UTC; however, the overnight operator "
        "closed it because CPU use was normal."
    )
    concise = (
        "The overnight operator closed the first alert at 02:17 UTC because CPU "
        "use was normal."
    )
    value, _ = engine(classifier=FakeClassifier(), runtime=FakeRuntime(()))

    validation = value.validator.validate(
        source,
        concise,
        task=PolishTask.REWRITE,
        grammar_mode=False,
    )

    assert validation.accepted

def test_validator_allows_every_dog_possessive_agreement_repair():
    source = "Every dog got their own personality."
    corrected = "Every dog has its own personality."
    runtime = FakeRuntime((corrected, corrected))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(request(source))

    assert result.text == corrected
    assert result.validation.accepted


def test_validator_allows_safe_repeated_actor_elision():
    source = "They walk every morning, and they play every evening."
    corrected = "They walk every morning and play every evening."
    runtime = FakeRuntime((corrected, corrected))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(request(source))

    assert result.text == corrected
    assert result.validation.accepted


def test_repeated_lowercase_plural_sentence_starter_is_not_protected_as_name():
    source = (
        "Dogs is really good pets because they is nice and they always doing stuff "
        "that make people happy but not every dog is the same because some is big and "
        "some is little and some bark all the time which can get annoying but thats "
        "okay mostly because dogs dont mean nothing bad by it."
    )
    corrected = (
        "Dogs are really good pets because they are nice and they are always doing "
        "things that make people happy, but not every dog is the same because some "
        "are big and some are little, and some bark all the time, which can get "
        "annoying, but that's mostly okay because dogs don't mean any harm by it."
    )
    runtime = FakeRuntime((corrected, corrected))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(request(source))

    assert result.text == corrected
    assert result.used_llm
    assert "Dogs" not in runtime.generate_calls[0].protected_terms


def test_common_adverb_sentence_starter_is_not_protected_as_name():
    source = "Sometimes they chew shoes."
    runtime = FakeRuntime((source,))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(request(source))

    assert result.used_llm
    assert "Sometimes" not in runtime.generate_calls[0].protected_terms


def test_common_full_page_sentence_starters_are_not_protected_as_names():
    cases = (
        ("Dogs also need food and exercise.", "Dogs"),
        ("People who own dogs often walk them.", "People"),
        ("Some dogs learn tricks quickly.", "Some"),
        ("One thing helps dogs feel safe.", "One"),
        ("Cleaning up after dogs takes work.", "Cleaning"),
        ("Having a dog takes patience.", "Having"),
    )
    for source, common_word in cases:
        runtime = FakeRuntime((source,))
        value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

        result = value.analyze(request(source))

        assert result.used_llm
        assert common_word not in runtime.generate_calls[0].protected_terms


def test_missing_apostrophe_repair_preserves_actor_inventory():
    source = "A dog stays close because its calm."
    corrected = "A dog stays close because it's calm."
    runtime = FakeRuntime((corrected, corrected))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(request(source))

    assert result.text == corrected
    assert result.validation.accepted


def test_too_much_typo_is_not_treated_as_an_obligation():
    source = "If a dog misses walks, they might have to much energy."
    corrected = "If a dog misses walks, they might have too much energy."
    runtime = FakeRuntime((corrected, corrected))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(request(source))

    assert result.text == corrected
    assert result.validation.accepted


def test_got_to_and_have_to_preserve_obligation_strength():
    source = "You got to take the dog to the vet."
    corrected = "You have to take the dog to the vet."
    runtime = FakeRuntime((corrected, corrected))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(request(source))

    assert result.text == corrected
    assert result.validation.accepted


def test_validator_still_rejects_removed_negation_after_sentence_split():
    source = "The dog is not dangerous, and it does not bite."
    unsafe = "The dog is dangerous. It bites."
    runtime = FakeRuntime((unsafe, unsafe))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(request(source))

    assert result.rejected_llm
    assert "not dangerous" in result.text
    assert "does not bite" in result.text


def test_balanced_retries_once_after_protected_fact_failure():
    runtime = FakeRuntime(
        (
            "Build 48 is not ready.",
            "Build 47 is not ready.",
        )
    )
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)
    result = value.analyze(request("Build 47 is not ready."))
    assert result.text == "Build 47 is not ready."
    assert result.used_llm
    assert result.retries == 1
    assert len(runtime.generate_calls) == 2
    assert "only safety retry" in runtime.generate_calls[1].instruction


def test_em_dash_in_candidate_is_converted_not_retried():
    # Permanent product rule (2026-08-01): corrections never contain an em
    # dash. A good candidate is converted in final cleanup instead of being
    # burned on a retry.
    runtime = FakeRuntime(
        (
            "The report is clear\u2014and complete.",
            "The report is clear and complete.",
        )
    )
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(request("The report is clear and complete."))

    assert "\u2014" not in result.text
    assert result.text == "The report is clear, and complete."
    assert result.quality_complete
    assert len(runtime.generate_calls) == 1


def test_quality_retry_repairs_the_previous_fact_safe_candidate():
    runtime = FakeRuntime(
        (
            "The reports is ready, and the files are ready.",
            "The reports are ready, and the files are ready.",
        )
    )
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        request("The reports is ready, and the files is ready.")
    )

    assert result.text == "The reports are ready, and the files are ready."
    assert result.quality_complete
    assert (
        runtime.generate_calls[1].text
        == "The reports is ready, and the files are ready."
    )


def test_failed_section_recovers_by_correcting_each_sentence_separately():
    source = "Build 47 is not ready. The reports is delayed."
    runtime = FakeRuntime(
        (
            "Build 48 is ready. The reports are delayed.",
            "Build 48 is not ready. The reports are delayed.",
            "Build 47 is not ready.",
            "The reports are delayed.",
        )
    )
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(request(source))

    assert result.text == "Build 47 is not ready. The reports are delayed."
    assert result.quality_complete
    assert result.review_ready
    assert len(runtime.generate_calls) == 4


def test_incomplete_fact_safe_ai_correction_is_not_review_ready():
    """Residual grammar keeps an otherwise safe AI answer out of review.

    The model never improves on its first answer here: it repairs the spelling
    and leaves the agreement error. Every retry is still spent trying for
    better, but an incomplete answer is not presented as ready for replacement.
    """
    runtime = FakeRuntime(
        (
            "The reports is ready.",
            "The reports is ready.",
            "The reports is ready.",
        )
    )
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(request("Teh reports is ready."))

    assert result.text == "The reports is ready."
    # Still not flawless, so the review card must not offer it for replacement.
    assert not result.quality_complete
    assert result.residual_errors
    assert result.validation.accepted
    assert not result.review_ready
    assert result.used_llm
    assert "not ready for review" in result.message.casefold()
    assert len(runtime.generate_calls) == 3


def test_rules_only_fallback_is_never_exposed_as_manual_correction():
    runtime = FakeRuntime(
        (
            "The build 48 is ready.",
            "The build 48 is ready.",
            "The build 48 is ready.",
        )
    )
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(request("Teh build 47 is not ready."))

    assert result.text == "The build 47 is not ready."
    assert not result.used_llm
    assert result.rejected_llm
    assert not result.review_ready


def test_sentence_recovery_requires_ai_for_every_sentence():
    source = "Teh build 47 is not ready. The reports is delayed."
    runtime = FakeRuntime(
        (
            "The build 48 is ready. The reports are delayed.",
            "The build 48 is not ready. The reports are delayed.",
            "The build 48 is not ready.",
            "The reports are delayed.",
        )
    )
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(request(source))

    assert result.text == "The build 47 is not ready. The reports is delayed."
    assert not result.used_llm
    assert result.rejected_llm
    assert not result.review_ready


def test_safety_retry_includes_exact_actor_inventory():
    runtime = FakeRuntime(
        (
            # Invents an actor the writer never named, which still refuses the
            # candidate outright and so forces the retry this test inspects.
            # Merely dropping the anchored "they" is an advisory now and would
            # be accepted on the first attempt.
            "The developers rushed because the client knew the deadline was close.",
            "The developers rushed because they knew the deadline was close.",
        )
    )
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)
    result = value.analyze(
        request("The developers rushed because they knew the deadline was close.")
    )

    assert result.used_llm
    retry = runtime.generate_calls[1].instruction.casefold()
    assert "actor inventory" in retry
    # Noun actors normalize to their singular family key ("developers" and
    # "developer" are the same actor), so the inventory reports the family.
    assert "developer=1" in retry
    assert "they=1" in retry


def test_safety_retry_forbids_new_pronoun_when_source_has_no_actor_inventory():
    source = "These numbers do not explain the result."
    runtime = FakeRuntime(
        (
            "These numbers are useful. They do not explain the result.",
            source,
        )
    )
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(request(source))

    assert result.used_llm
    retry = runtime.generate_calls[1].instruction.casefold()
    assert "source has no pronoun actor" in retry
    assert "do not add i, we, you, he, she, they, or it" in retry


def test_safety_retry_includes_exact_negation_inventory():
    source = "These numbers do not explain the result."
    runtime = FakeRuntime(("These numbers explain the result.", source))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(request(source))

    assert result.used_llm
    retry = runtime.generate_calls[1].instruction.casefold()
    assert "negation inventory" in retry
    assert "not=1" in retry


def test_sentence_recovery_repeats_actor_and_negation_inventories():
    source = "These reports is not ready."
    corrected = "These reports are not ready."
    unsafe = "These reports are useful. They are ready."
    runtime = FakeRuntime((unsafe, unsafe, corrected))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(request(source))

    assert result.used_llm
    assert not result.rejected_llm
    assert result.text == corrected


def test_safety_retry_includes_exact_name_inventory():
    runtime = FakeRuntime(
        (
            "Mali will correct the notice.",
            "Malik will correct the notice.",
        )
    )
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(request("Malik will correct the notice."))

    assert result.used_llm
    retry = runtime.generate_calls[1].instruction
    assert "exact name inventory" in retry.casefold()
    assert "Malik" in retry


def test_generation_protects_detected_names_before_qwen():
    runtime = FakeRuntime(("Malik will correct the notice.",))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(request("Malik will correct the notice."))

    assert result.used_llm
    assert "Malik" in runtime.generate_calls[0].protected_terms


def test_engine_applies_fact_safe_final_cleanup_to_runtime_candidate():
    runtime = FakeRuntime(
        ("After the all clear staff compare logs. A survey shows interest, not guaranteed attendance.",)
    )
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        request(
            "After the all clear staff compare logs. "
            "A survey shows interest, not guaranteed attendance."
        )
    )

    assert result.text == (
        "After the all-clear, staff compare logs. "
        "A survey shows interest but does not guarantee attendance."
    )


def test_generation_does_not_tokenize_month_as_person_name():
    runtime = FakeRuntime(("Malik will correct it on September 13.",))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(request("Malik will correct it on September 13."))

    assert result.used_llm
    assert "Malik" in runtime.generate_calls[0].protected_terms
    assert "September" not in runtime.generate_calls[0].protected_terms


def test_generation_protects_product_and_version_as_one_literal():
    runtime = FakeRuntime(("It works on Windows 11 and Ubuntu 24.04.",))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(request("It works on Windows 11 and Ubuntu 24.04."))

    assert result.used_llm
    protected = runtime.generate_calls[0].protected_terms
    assert "Windows 11" in protected
    assert "Ubuntu 24.04" in protected
    assert "Windows" not in protected
    assert "Ubuntu" not in protected


def test_generation_does_not_split_honorific_name_into_opaque_tokens():
    runtime = FakeRuntime(("Dr. Amina Shah says the drill is ready.",))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(request("Dr. Amina Shah say the drill is ready."))

    assert result.used_llm
    protected = runtime.generate_calls[0].protected_terms
    assert "Dr. Amina Shah" not in protected
    assert "Dr" not in protected
    assert "Amina Shah" not in protected


def test_generation_does_not_treat_dates_as_product_versions():
    runtime = FakeRuntime(("The deadline is October 8.",))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(request("The deadline is October 8."))

    assert result.used_llm
    assert "October 8" not in runtime.generate_calls[0].protected_terms


def test_generation_does_not_tokenize_common_sentence_starters_as_names():
    runtime = FakeRuntime(
        (
            "At intake, Daniel checks it. Each station is ready. "
            "Operations coordinator Malik Brooks approves it. "
            "Technicians test it. Until then, staff waits. "
            "Project planning continues. Funding remains limited. "
            "Success measures are listed. This was a serious mistake.",
            "At intake, Daniel checks it. Each station is ready. "
            "Operations coordinator Malik Brooks approves it. "
            "Technicians test it. Until then, staff waits. "
            "Project planning continues. Funding remains limited. "
            "Success measures are listed. This was a serious mistake.",
        )
    )
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        request(
            "At intake, Daniel checks it. Each station is ready. "
            "Operations coordinator Malik Brooks approves it. "
            "Technicians test it. Until then, staff waits. "
            "Project planning continues. Funding remains limited. "
            "Success measures are listed. Which was a serious mistake."
        )
    )

    assert result.used_llm
    protected = runtime.generate_calls[0].protected_terms
    assert "Daniel" in protected
    assert "Malik Brooks" not in protected
    assert "At" not in protected
    assert "Each" not in protected
    assert "Operations" not in protected
    assert "Technicians" not in protected
    assert "Until" not in protected
    assert "Project" not in protected
    assert "Funding" not in protected
    assert "Success" not in protected
    assert "Which" not in protected


def test_instant_does_not_retry_unsafe_ai_output():
    runtime = FakeRuntime(("Build 48 is not ready.",))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)
    value.packs = FakePacks(
        replace(
            model_pack(),
            pack_id="gemma4-e2b-qat-v1",
            display_name="Gemma 4 E2B QAT",
            model="gemma4:e2b-it-qat",
            parameters="2.3B effective",
            size_bytes=5 * GIB,
            min_ram_bytes=8 * GIB,
            min_vram_bytes=6 * GIB,
        )
    )
    result = value.analyze(request("Build 47 is not ready.", mode=PolishMode.INSTANT))
    assert result.text == "Build 47 is not ready."
    assert result.rejected_llm
    assert len(runtime.generate_calls) == 1


def test_runtime_failure_keeps_original_text_and_marks_quality_incomplete():
    runtime = FakeRuntime(error="GPU was lost")
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)
    result = value.analyze(request("Teh reports is not ready."))
    assert result.text == "The reports is not ready."
    assert not result.used_llm
    assert not result.quality_complete
    assert not result.review_ready
    assert result.runtime_failure_kind is RuntimeFailureKind.GPU_FAILURE
    assert "GPU was lost" in result.message


def test_missing_download_consent_does_not_poison_the_signed_pack():
    pack = replace(model_pack(), installed=False, validated=False)

    class ConsentPacks(FakePacks):
        def ensure(self, plan, *, consent, progress=None):
            self.ensure_calls += 1
            assert not consent
            raise ModelPackConsentRequired(self.pack)

    packs = ConsentPacks(pack)
    value = PolishEngine(
        rules=FakeRules(),
        classifier=FakeClassifier(),
        packs=packs,
        runtime=FakeRuntime(()),
        hardware_provider=hardware,
        benchmark_cache=PolishBenchmarkCache(MemoryStore()),
    )

    first = value.analyze(request("Teh reports is not ready."))
    second = value.analyze(request("Teh reports is not ready."))

    assert packs.ensure_calls == 2
    assert value._failed_pack_ids() == set()
    assert first.runtime_failure_kind is RuntimeFailureKind.MODEL_UNAVAILABLE
    assert second.runtime_failure_kind is RuntimeFailureKind.MODEL_UNAVAILABLE
    assert "download approval" in second.message.casefold()


def test_successful_ai_result_exposes_quality_fields():
    runtime = FakeRuntime(("The reports are not ready, and it fails.",))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)
    result = value.analyze(request("The reports is not ready, and it fail."))
    assert result.quality_complete
    assert result.residual_errors == ()
    assert result.tone_verified
    assert 0.0 <= result.quality_score <= 1.0
    assert result.runtime_failure_kind is RuntimeFailureKind.NONE
    assert result.cold_load_seconds >= 0.0
    assert result.generation_seconds == 0.25


def test_successful_tone_result_reports_rewrite_ready_not_old_correction_state():
    runtime = FakeRuntime(("The report is polished and ready.",))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)

    result = value.analyze(
        CorrectionRequest(
            "The report is ready.",
            task=PolishTask.REWRITE,
            mode=PolishMode.BALANCED,
            command="Professional",
            manual=True,
        )
    )

    assert result.validation.accepted
    assert "rewrite ready" in result.message.casefold()


def test_failed_acceptance_probe_does_not_run_the_rejected_model():
    runtime = FakeRuntime(("The reports are not ready.",), probe=False)
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)
    result = value.analyze(request("The reports is not ready."))

    assert not result.used_llm
    assert runtime.generate_calls == []
    assert result.plan.backend is RuntimeBackend.RULES
    assert not result.review_ready
    assert "failed its local acceptance probe" in result.plan.downgrade_reason
    assert "gemma" not in result.plan.downgrade_reason.casefold()


def test_verified_warm_plan_is_reused_without_reprobing_later_sections():
    pack = model_pack()
    runtime = FakeRuntime((), probe=False)
    runtime.is_current = lambda pack_id, model, policy_version, **_kwargs: (
        pack_id == pack.pack_id
        and model == pack.model
        and policy_version == probe_plan(pack).policy_version
    )
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)
    plan = probe_plan(pack)

    accepted = value._accept_plan(plan, hardware(), pack)

    assert accepted.use_llm
    assert not accepted.requires_probe
    assert runtime.probe_calls == 0


def test_failed_section_does_not_blacklist_current_verified_pack():
    pack = model_pack()
    runtime = FakeRuntime(("The reports are ready.",))
    plan = probe_plan(pack)
    runtime.lease = lambda: SimpleNamespace(plan=plan)
    runtime.is_current = lambda *_args, **_kwargs: True
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)
    value._remember_failed_pack(
        pack.pack_id,
        "validation failed for one section",
        kind=RuntimeFailureKind.VALIDATION_FAILURE,
    )

    result = value.analyze(request("The reports is ready."))

    assert result.plan.pack_id == pack.pack_id
    assert result.used_llm
    assert len(runtime.generate_calls) == 1


def test_slow_only_pack_is_rejected_for_manual_correction():
    pack = model_pack()
    store = MemoryStore()
    cache = PolishBenchmarkCache(store)
    snapshot = hardware()
    runtime = FakeRuntime(())
    cache.put(
        cache.key(
            snapshot,
            runtime_id=runtime.runtime_id,
            pack=pack,
            plan=probe_plan(pack),
        ),
        BenchmarkRecord(
            accepted=False,
            correctness_passed=True,
            protected_passed=True,
            elapsed_seconds=6.3,
            backend="cuda",
            reason="warm latency exceeded 5 seconds",
            measured_at=datetime.now(timezone.utc).isoformat(),
        ),
    )
    value = PolishEngine(
        rules=FakeRules(),
        classifier=FakeClassifier(),
        packs=FakePacks(pack),
        runtime=runtime,
        hardware_provider=lambda: snapshot,
        benchmark_cache=cache,
    )
    plan = probe_plan(pack)

    accepted = value._accept_plan(plan, snapshot, pack)

    assert not accepted.use_llm
    assert accepted.pack_id == ""
    assert accepted.backend is RuntimeBackend.RULES
    assert "failed its local acceptance probe" in accepted.downgrade_reason


def test_manual_engine_without_download_consent_rejects_failed_installed_pack():
    installed_gemma = replace(
        model_pack(),
        pack_id="gemma4-12b-q4-k-m-v1",
        display_name="Gemma 4 12B Q4_K_M",
        model="gemma4:12b-it-q4_K_M",
        family="Gemma 4",
        parameters="12B",
        size_bytes=8 * GIB,
        installed=True,
    )
    uninstalled_small = replace(
        model_pack(),
        pack_id="gemma4-e2b-qat-v1",
        display_name="Gemma 4 E2B QAT",
        model="gemma4:e2b-it-qat",
        parameters="2.3B effective",
        quantization="Q4_0",
        size_bytes=5 * GIB,
        min_ram_bytes=8 * GIB,
        min_vram_bytes=0,
        supported_backends=(RuntimeBackend.CUDA, RuntimeBackend.CPU),
        installed=False,
    )

    class CatalogPacks:
        def __init__(self):
            self.ensure_ids = []

        def catalog(self, *, refresh_installed=True):
            return (installed_gemma, uninstalled_small)

        def ensure(self, plan, *, consent, progress=None):
            self.ensure_ids.append(plan.pack_id)
            if plan.pack_id == uninstalled_small.pack_id and not consent:
                raise AssertionError("uninstalled pack must not be selected without consent")
            return installed_gemma

    packs = CatalogPacks()
    store = MemoryStore()
    cache = PolishBenchmarkCache(store)
    snapshot = hardware()
    runtime = FakeRuntime(("The reports are not ready.",))
    cache.put(
        cache.key(
            snapshot,
            runtime_id=runtime.runtime_id,
            pack=installed_gemma,
            plan=probe_plan(installed_gemma),
        ),
        BenchmarkRecord(
            accepted=False,
            correctness_passed=True,
            protected_passed=True,
            elapsed_seconds=6.3,
            backend="cuda",
            reason="warm latency exceeded 5 seconds",
            measured_at=datetime.now(timezone.utc).isoformat(),
        ),
    )
    value = PolishEngine(
        rules=FakeRules(),
        classifier=FakeClassifier(),
        packs=packs,
        runtime=runtime,
        hardware_provider=lambda: snapshot,
        benchmark_cache=cache,
    )

    result = value.analyze(request("The reports is not ready."))

    assert not result.used_llm
    assert not result.review_ready
    assert packs.ensure_ids == []


def test_failed_larger_pack_prepares_and_uses_installed_smaller_pack():
    larger_pack = replace(
        model_pack(),
        pack_id="gemma4-e4b-qat-v1",
        display_name="Gemma 4 E4B QAT",
        model="gemma4:e4b-it-qat",
        parameters="4.5B effective",
        size_bytes=6 * GIB,
        installed=True,
    )
    smaller_pack = replace(
        model_pack(),
        pack_id="gemma4-e2b-qat-v1",
        display_name="Gemma 4 E2B QAT",
        model="gemma4:e2b-it-qat",
        parameters="2.3B effective",
        size_bytes=5 * GIB,
        min_ram_bytes=8 * GIB,
        min_vram_bytes=6 * GIB,
        installed=True,
    )

    class DownloadingPacks:
        def __init__(self):
            self.ensure_ids = []

        def catalog(self, *, refresh_installed=True):
            return (larger_pack, smaller_pack)

        def ensure(self, plan, *, consent, progress=None):
            self.ensure_ids.append(plan.pack_id)
            if plan.pack_id == smaller_pack.pack_id:
                assert not consent
                return smaller_pack
            return larger_pack

    class RecordingRuntime(FakeRuntime):
        def __init__(self):
            super().__init__(("The reports are not ready.",))
            self.prepare_ids = []
            self.probe_ids = []

        def prepare(self, plan, *, consent=False):
            self.prepare_ids.append(plan.pack_id)
            return True

        def acceptance_probe(self, plan):
            self.probe_ids.append(plan.pack_id)
            accepted = plan.pack_id == smaller_pack.pack_id
            return AcceptanceProbeResult(
                accepted=accepted,
                elapsed_seconds=0.5 if accepted else 6.3,
                correctness_passed=True,
                protected_passed=True,
                backend=RuntimeBackend.CUDA,
                reason="" if accepted else "warm latency exceeded 5 seconds",
            )

    packs = DownloadingPacks()
    runtime = RecordingRuntime()
    value = PolishEngine(
        rules=FakeRules(),
        classifier=FakeClassifier(),
        packs=packs,
        runtime=runtime,
        hardware_provider=hardware,
        benchmark_cache=PolishBenchmarkCache(MemoryStore()),
    )

    result = value.analyze(
        replace(
            request("The reports is not ready."),
            download_consent=True,
        )
    )

    assert result.used_llm
    assert result.review_ready
    assert result.text == "The reports are not ready."
    assert result.plan.pack_id == smaller_pack.pack_id
    assert packs.ensure_ids == [larger_pack.pack_id, smaller_pack.pack_id]
    assert runtime.prepare_ids == [larger_pack.pack_id, smaller_pack.pack_id]
    assert runtime.probe_ids == [larger_pack.pack_id, smaller_pack.pack_id]
    assert [call.plan.pack_id for call in runtime.generate_calls] == [
        smaller_pack.pack_id
    ]
    assert result.plan.backend is RuntimeBackend.CUDA
    assert result.plan.require_full_gpu


def test_failed_installed_fallback_stops_after_one_attempt_per_pack():
    larger_pack = model_pack()
    smaller_pack = replace(
        model_pack(),
        pack_id="gemma4-e2b-qat-v1",
        display_name="Gemma 4 E2B QAT",
        model="gemma4:e2b-it-qat",
        parameters="2.3B effective",
        size_bytes=5 * GIB,
        min_ram_bytes=8 * GIB,
        min_vram_bytes=6 * GIB,
        installed=True,
    )

    class DownloadingPacks:
        def __init__(self):
            self.ensure_ids = []

        def catalog(self, *, refresh_installed=True):
            return (larger_pack, smaller_pack)

        def ensure(self, plan, *, consent, progress=None):
            self.ensure_ids.append(plan.pack_id)
            if plan.pack_id == smaller_pack.pack_id:
                assert not consent
            selected = next(
                pack for pack in self.catalog() if pack.pack_id == plan.pack_id
            )
            return selected

    class RejectingRuntime(FakeRuntime):
        def __init__(self):
            super().__init__(())
            self.prepare_ids = []
            self.probe_ids = []

        def prepare(self, plan, *, consent=False):
            self.prepare_ids.append(plan.pack_id)
            return True

        def acceptance_probe(self, plan):
            self.probe_ids.append(plan.pack_id)
            return AcceptanceProbeResult(
                accepted=False,
                elapsed_seconds=6.3,
                correctness_passed=True,
                protected_passed=True,
                backend=RuntimeBackend.CUDA,
                reason="warm latency exceeded 5 seconds",
            )

    packs = DownloadingPacks()
    runtime = RejectingRuntime()
    value = PolishEngine(
        rules=FakeRules(),
        classifier=FakeClassifier(),
        packs=packs,
        runtime=runtime,
        hardware_provider=hardware,
        benchmark_cache=PolishBenchmarkCache(MemoryStore()),
    )

    result = value.analyze(
        replace(
            request("The reports is not ready."),
            download_consent=True,
        )
    )

    assert not result.used_llm
    assert not result.review_ready
    assert packs.ensure_ids == [larger_pack.pack_id, smaller_pack.pack_id]
    assert runtime.prepare_ids == [larger_pack.pack_id, smaller_pack.pack_id]
    assert runtime.probe_ids == [larger_pack.pack_id, smaller_pack.pack_id]
    assert runtime.generate_calls == []


def test_primary_consent_never_downloads_an_uninstalled_fallback_pack():
    larger_pack = model_pack()
    smaller_pack = replace(
        model_pack(),
        pack_id="gemma4-e2b-qat-v1",
        display_name="Gemma 4 E2B QAT",
        model="gemma4:e2b-it-qat",
        parameters="2.3B effective",
        size_bytes=5 * GIB,
        min_ram_bytes=8 * GIB,
        min_vram_bytes=6 * GIB,
        installed=False,
    )

    class Packs:
        def __init__(self):
            self.ensure_ids = []

        def catalog(self, *, refresh_installed=True):
            return (larger_pack, smaller_pack)

        def ensure(self, plan, *, consent, progress=None):
            self.ensure_ids.append(plan.pack_id)
            return larger_pack if plan.pack_id == larger_pack.pack_id else smaller_pack

    class Runtime(FakeRuntime):
        def __init__(self):
            super().__init__(())
            self.probe_ids = []

        def acceptance_probe(self, plan):
            self.probe_ids.append(plan.pack_id)
            accepted = plan.pack_id == smaller_pack.pack_id
            return AcceptanceProbeResult(
                accepted=accepted,
                elapsed_seconds=0.5,
                correctness_passed=accepted,
                protected_passed=accepted,
                backend=RuntimeBackend.CUDA,
                reason="primary probe failed" if not accepted else "",
            )

    packs = Packs()
    runtime = Runtime()
    value = PolishEngine(
        rules=FakeRules(),
        classifier=FakeClassifier(),
        packs=packs,
        runtime=runtime,
        hardware_provider=hardware,
        benchmark_cache=PolishBenchmarkCache(MemoryStore()),
    )

    result = value.analyze(
        replace(request("The reports is not ready."), download_consent=True)
    )

    assert not result.used_llm
    assert not result.review_ready
    assert packs.ensure_ids == [larger_pack.pack_id]
    assert runtime.probe_ids == [larger_pack.pack_id]


@pytest.mark.parametrize(
    ("failure_stage", "failure"),
    (
        ("ensure", ModelPackDownloadError("primary download failed")),
        ("ensure", ModelPackSecurityError("primary signature failed")),
        ("prepare", RuntimeError("primary runtime preparation failed")),
    ),
)
def test_primary_activation_failure_uses_next_smaller_pack(
    failure_stage,
    failure,
):
    larger_pack = model_pack()
    smaller_pack = replace(
        model_pack(),
        pack_id="gemma4-e2b-qat-v1",
        display_name="Gemma 4 E2B QAT",
        model="gemma4:e2b-it-qat",
        parameters="2.3B effective",
        size_bytes=5 * GIB,
        min_ram_bytes=8 * GIB,
        min_vram_bytes=6 * GIB,
        installed=True,
    )

    class ActivatingPacks:
        def __init__(self):
            self.ensure_ids = []

        def catalog(self, *, refresh_installed=True):
            return (larger_pack, smaller_pack)

        def ensure(self, plan, *, consent, progress=None):
            self.ensure_ids.append(plan.pack_id)
            if plan.pack_id == larger_pack.pack_id and failure_stage == "ensure":
                raise failure
            if plan.pack_id == smaller_pack.pack_id:
                assert not consent
                return smaller_pack
            return larger_pack

    class ActivatingRuntime(FakeRuntime):
        def __init__(self):
            super().__init__(("The reports are not ready.",))
            self.prepare_ids = []
            self.probe_ids = []

        def prepare(self, plan, *, consent=False):
            self.prepare_ids.append(plan.pack_id)
            if plan.pack_id == larger_pack.pack_id and failure_stage == "prepare":
                raise failure
            return True

        def acceptance_probe(self, plan):
            self.probe_ids.append(plan.pack_id)
            return AcceptanceProbeResult(
                accepted=True,
                elapsed_seconds=0.5,
                correctness_passed=True,
                protected_passed=True,
                backend=RuntimeBackend.CUDA,
            )

    packs = ActivatingPacks()
    runtime = ActivatingRuntime()
    value = PolishEngine(
        rules=FakeRules(),
        classifier=FakeClassifier(),
        packs=packs,
        runtime=runtime,
        hardware_provider=hardware,
        benchmark_cache=PolishBenchmarkCache(MemoryStore()),
    )

    result = value.analyze(
        replace(
            request("The reports is not ready."),
            download_consent=True,
        )
    )

    assert result.used_llm
    assert result.review_ready
    assert result.plan.pack_id == smaller_pack.pack_id
    assert packs.ensure_ids == [larger_pack.pack_id, smaller_pack.pack_id]
    expected_prepare = (
        [larger_pack.pack_id, smaller_pack.pack_id]
        if failure_stage == "prepare"
        else [smaller_pack.pack_id]
    )
    assert runtime.prepare_ids == expected_prepare
    assert runtime.probe_ids == [smaller_pack.pack_id]
    assert [call.plan.pack_id for call in runtime.generate_calls] == [
        smaller_pack.pack_id
    ]


def test_terminal_activation_failure_kind_matches_final_failure_message():
    larger_pack = model_pack()
    smaller_pack = replace(
        model_pack(),
        pack_id="gemma4-e2b-qat-v1",
        display_name="Gemma 4 E2B QAT",
        model="gemma4:e2b-it-qat",
        parameters="2.3B effective",
        size_bytes=5 * GIB,
        min_ram_bytes=8 * GIB,
        min_vram_bytes=6 * GIB,
        installed=True,
    )

    class FailingPacks:
        def catalog(self, *, refresh_installed=True):
            return (larger_pack, smaller_pack)

        def ensure(self, plan, *, consent, progress=None):
            if plan.pack_id == larger_pack.pack_id:
                raise RuntimeError("primary GPU failed")
            raise RuntimeError("final model load timed out")

    value = PolishEngine(
        rules=FakeRules(),
        classifier=FakeClassifier(),
        packs=FailingPacks(),
        runtime=FakeRuntime(()),
        hardware_provider=hardware,
        benchmark_cache=PolishBenchmarkCache(MemoryStore()),
    )

    result = value.analyze(
        replace(request("The reports is not ready."), download_consent=True)
    )

    assert not result.used_llm
    assert "final model load timed out" in result.message
    assert "final model load timed out" in result.plan.downgrade_reason
    assert result.runtime_failure_kind is RuntimeFailureKind.LOAD_TIMEOUT


@pytest.mark.parametrize(
    "failure_message",
    (
        "GPU lost full residency",
        "generation timed out",
        "model load timed out",
        "Local writing model returned invalid text",
        "Local writing model returned incomplete text",
        "Local writing model changed protected content",
        "Local writing model changed a meaning constraint",
    ),
)
def test_generation_runtime_failure_uses_next_smaller_accepted_pack(
    failure_message,
):
    larger_pack = model_pack()
    smaller_pack = replace(
        model_pack(),
        pack_id="gemma4-e2b-qat-v1",
        display_name="Gemma 4 E2B QAT",
        model="gemma4:e2b-it-qat",
        parameters="2.3B effective",
        size_bytes=5 * GIB,
        min_ram_bytes=8 * GIB,
        min_vram_bytes=6 * GIB,
        installed=True,
    )

    class DownloadingPacks:
        def __init__(self):
            self.ensure_ids = []

        def catalog(self, *, refresh_installed=True):
            return (larger_pack, smaller_pack)

        def ensure(self, plan, *, consent, progress=None):
            self.ensure_ids.append(plan.pack_id)
            if plan.pack_id == smaller_pack.pack_id:
                assert not consent
                return smaller_pack
            return larger_pack

    class FailingPrimaryRuntime(FakeRuntime):
        def __init__(self):
            super().__init__(())
            self.prepare_ids = []
            self.prepare_consents = []
            self.probe_ids = []
            self.generate_ids = []

        def prepare(self, plan, *, consent=False):
            self.prepare_ids.append(plan.pack_id)
            self.prepare_consents.append(consent)
            return True

        def acceptance_probe(self, plan):
            self.probe_ids.append(plan.pack_id)
            return AcceptanceProbeResult(
                accepted=True,
                elapsed_seconds=0.5,
                correctness_passed=True,
                protected_passed=True,
                backend=RuntimeBackend.CUDA,
            )

        def generate(self, generation_request):
            self.generate_calls.append(generation_request)
            self.generate_ids.append(generation_request.plan.pack_id)
            if generation_request.plan.pack_id == larger_pack.pack_id:
                raise RuntimeError(failure_message)
            return GenerationResult(
                "The reports are not ready.",
                generation_request.plan.model,
                RuntimeBackend.CUDA,
                0.25,
            )

    packs = DownloadingPacks()
    runtime = FailingPrimaryRuntime()
    value = PolishEngine(
        rules=FakeRules(),
        classifier=FakeClassifier(),
        packs=packs,
        runtime=runtime,
        hardware_provider=hardware,
        benchmark_cache=PolishBenchmarkCache(MemoryStore()),
    )

    result = value.analyze(
        replace(
            request("The reports is not ready."),
            download_consent=True,
        )
    )
    second = value.analyze(
        replace(
            request("The reports is not ready."),
            download_consent=True,
        )
    )

    assert result.used_llm
    assert result.review_ready
    assert result.text == "The reports are not ready."
    assert result.plan.pack_id == smaller_pack.pack_id
    assert failure_message in result.plan.downgrade_reason
    assert second.used_llm
    assert second.plan.pack_id == smaller_pack.pack_id
    assert failure_message in second.plan.downgrade_reason
    assert packs.ensure_ids == [
        larger_pack.pack_id,
        smaller_pack.pack_id,
        smaller_pack.pack_id,
    ]
    assert runtime.prepare_ids == [
        larger_pack.pack_id,
        smaller_pack.pack_id,
        smaller_pack.pack_id,
    ]
    assert runtime.prepare_consents == [False, False, False]
    assert runtime.probe_ids == [larger_pack.pack_id, smaller_pack.pack_id]
    assert runtime.generate_ids == [
        larger_pack.pack_id,
        smaller_pack.pack_id,
        smaller_pack.pack_id,
    ]


def test_failed_pack_poison_expires_after_five_minutes():
    larger_pack = model_pack()
    smaller_pack = replace(
        model_pack(),
        pack_id="gemma4-e2b-qat-v1",
        display_name="Gemma 4 E2B QAT",
        model="gemma4:e2b-it-qat",
        parameters="2.3B effective",
        size_bytes=5 * GIB,
        min_ram_bytes=8 * GIB,
        min_vram_bytes=6 * GIB,
        installed=True,
    )
    clock = [1000.0]

    class Packs:
        def __init__(self):
            self.ensure_ids = []

        def catalog(self, *, refresh_installed=True):
            return (larger_pack, smaller_pack)

        def ensure(self, plan, *, consent, progress=None):
            self.ensure_ids.append(plan.pack_id)
            return larger_pack if plan.pack_id == larger_pack.pack_id else smaller_pack

    class Runtime(FakeRuntime):
        def __init__(self):
            super().__init__(())
            self.generate_ids = []

        def generate(self, generation_request):
            self.generate_calls.append(generation_request)
            self.generate_ids.append(generation_request.plan.pack_id)
            if generation_request.plan.pack_id == larger_pack.pack_id:
                raise RuntimeError("generation timed out")
            return GenerationResult(
                "The reports are not ready.",
                generation_request.plan.model,
                RuntimeBackend.CUDA,
                0.25,
            )

    packs = Packs()
    runtime = Runtime()
    value = PolishEngine(
        rules=FakeRules(),
        classifier=FakeClassifier(),
        packs=packs,
        runtime=runtime,
        hardware_provider=hardware,
        benchmark_cache=PolishBenchmarkCache(MemoryStore()),
        monotonic=lambda: clock[0],
    )

    first = value.analyze(request("The reports is not ready."))
    clock[0] = 1299.9
    second = value.analyze(request("The reports is not ready."))
    clock[0] = 1300.1
    third = value.analyze(request("The reports is not ready."))

    assert first.plan.pack_id == smaller_pack.pack_id
    assert second.plan.pack_id == smaller_pack.pack_id
    assert third.plan.pack_id == smaller_pack.pack_id
    assert runtime.generate_ids == [
        larger_pack.pack_id,
        smaller_pack.pack_id,
        smaller_pack.pack_id,
        larger_pack.pack_id,
        smaller_pack.pack_id,
    ]


def test_protected_validation_failure_uses_installed_smaller_pack():
    larger_pack = model_pack()
    smaller_pack = replace(
        model_pack(),
        pack_id="gemma4-e2b-qat-v1",
        display_name="Gemma 4 E2B QAT",
        model="gemma4:e2b-it-qat",
        parameters="2.3B effective",
        size_bytes=5 * GIB,
        min_ram_bytes=8 * GIB,
        min_vram_bytes=6 * GIB,
        installed=True,
    )

    class Packs:
        def catalog(self, *, refresh_installed=True):
            return (larger_pack, smaller_pack)

        def ensure(self, plan, *, consent, progress=None):
            if plan.pack_id == smaller_pack.pack_id:
                assert not consent
                return smaller_pack
            return larger_pack

    class Runtime(FakeRuntime):
        def __init__(self):
            super().__init__(())
            self.generate_ids = []

        def prepare(self, plan, *, consent=False):
            if plan.pack_id == smaller_pack.pack_id:
                assert not consent
            return True

        def generate(self, generation_request):
            self.generate_calls.append(generation_request)
            self.generate_ids.append(generation_request.plan.pack_id)
            output = (
                "Build 48 is ready."
                if generation_request.plan.pack_id == larger_pack.pack_id
                else "Build 47 is not ready."
            )
            return GenerationResult(
                output,
                generation_request.plan.model,
                RuntimeBackend.CUDA,
                0.25,
            )

    runtime = Runtime()
    value = PolishEngine(
        rules=FakeRules(),
        classifier=FakeClassifier(),
        packs=Packs(),
        runtime=runtime,
        hardware_provider=hardware,
        benchmark_cache=PolishBenchmarkCache(MemoryStore()),
    )

    result = value.analyze(request("Build 47 is not ready."))

    assert result.used_llm
    assert result.review_ready
    assert result.text == "Build 47 is not ready."
    assert result.plan.pack_id == smaller_pack.pack_id
    assert smaller_pack.pack_id in runtime.generate_ids


@pytest.mark.parametrize("failure_stage", ("ensure", "prepare", "probe"))
def test_activation_cancellation_propagates_without_poisoning_or_fallback(
    failure_stage,
):
    larger_pack = model_pack()
    smaller_pack = replace(
        model_pack(),
        pack_id="gemma4-e2b-qat-v1",
        display_name="Gemma 4 E2B QAT",
        model="gemma4:e2b-it-qat",
        parameters="2.3B effective",
        size_bytes=5 * GIB,
        min_ram_bytes=8 * GIB,
        min_vram_bytes=6 * GIB,
        installed=True,
    )

    class Packs:
        def __init__(self):
            self.ensure_ids = []

        def catalog(self, *, refresh_installed=True):
            return (larger_pack, smaller_pack)

        def ensure(self, plan, *, consent, progress=None):
            self.ensure_ids.append(plan.pack_id)
            if failure_stage == "ensure":
                raise RuntimeError("operation cancelled by user")
            return larger_pack

    class Runtime(FakeRuntime):
        def prepare(self, plan, *, consent=False):
            if failure_stage == "prepare":
                raise RuntimeError("operation cancelled by user")
            return True

        def acceptance_probe(self, plan):
            if failure_stage == "probe":
                raise RuntimeError("operation cancelled by user")
            return super().acceptance_probe(plan)

    packs = Packs()
    value = PolishEngine(
        rules=FakeRules(),
        classifier=FakeClassifier(),
        packs=packs,
        runtime=Runtime(()),
        hardware_provider=hardware,
        benchmark_cache=PolishBenchmarkCache(MemoryStore()),
    )

    with pytest.raises(RuntimeError, match="cancelled"):
        value.analyze(request("The reports is not ready."))

    assert packs.ensure_ids == [larger_pack.pack_id]
    assert smaller_pack.pack_id not in packs.ensure_ids
    assert value._failed_pack_ids() == set()


def test_fallback_activation_cancellation_does_not_poison_smaller_pack():
    larger_pack = model_pack()
    smaller_pack = replace(
        model_pack(),
        pack_id="gemma4-e2b-qat-v1",
        display_name="Gemma 4 E2B QAT",
        model="gemma4:e2b-it-qat",
        parameters="2.3B effective",
        size_bytes=5 * GIB,
        min_ram_bytes=8 * GIB,
        min_vram_bytes=6 * GIB,
        installed=True,
    )

    class Packs:
        def __init__(self):
            self.ensure_ids = []

        def catalog(self, *, refresh_installed=True):
            return (larger_pack, smaller_pack)

        def ensure(self, plan, *, consent, progress=None):
            self.ensure_ids.append(plan.pack_id)
            return next(
                pack for pack in self.catalog() if pack.pack_id == plan.pack_id
            )

    class Runtime(FakeRuntime):
        def prepare(self, plan, *, consent=False):
            if plan.pack_id == smaller_pack.pack_id:
                raise RuntimeError("operation cancelled by user")
            return True

        def acceptance_probe(self, plan):
            accepted = plan.pack_id == smaller_pack.pack_id
            return AcceptanceProbeResult(
                accepted=accepted,
                elapsed_seconds=0.5,
                correctness_passed=accepted,
                protected_passed=accepted,
                backend=RuntimeBackend.CUDA,
                reason="primary probe failed" if not accepted else "",
            )

    packs = Packs()
    value = PolishEngine(
        rules=FakeRules(),
        classifier=FakeClassifier(),
        packs=packs,
        runtime=Runtime(()),
        hardware_provider=hardware,
        benchmark_cache=PolishBenchmarkCache(MemoryStore()),
    )

    with pytest.raises(RuntimeError, match="cancelled"):
        value.analyze(request("The reports is not ready."))

    assert packs.ensure_ids == [larger_pack.pack_id, smaller_pack.pack_id]
    assert larger_pack.pack_id in value._failed_pack_ids()
    assert smaller_pack.pack_id not in value._failed_pack_ids()


def test_runtime_fallback_never_moves_to_a_larger_pack():
    smaller_pack = replace(
        model_pack(),
        pack_id="gemma4-e2b-qat-v1",
        display_name="Gemma 4 E2B QAT",
        model="gemma4:e2b-it-qat",
        parameters="2.3B effective",
        size_bytes=5 * GIB,
        min_ram_bytes=8 * GIB,
        min_vram_bytes=6 * GIB,
        installed=True,
    )
    larger_pack = model_pack()

    class Packs:
        def __init__(self):
            self.ensure_ids = []

        def catalog(self, *, refresh_installed=True):
            return (smaller_pack, larger_pack)

        def ensure(self, plan, *, consent, progress=None):
            self.ensure_ids.append(plan.pack_id)
            return next(
                pack for pack in self.catalog() if pack.pack_id == plan.pack_id
            )

    packs = Packs()
    classifier = FakeClassifier()
    value = PolishEngine(
        rules=FakeRules(),
        classifier=classifier,
        packs=packs,
        runtime=FakeRuntime(()),
        hardware_provider=hardware,
        benchmark_cache=PolishBenchmarkCache(MemoryStore()),
    )
    decision = classifier.classify(
        "The reports is not ready.",
        "The reports is not ready.",
        task=PolishTask.PROOFREAD,
    )

    fallback = value._accept_smaller_plan(
        request=request("The reports is not ready."),
        classifier=decision,
        snapshot=hardware(),
        rejected_plan=probe_plan(smaller_pack),
        attempted_pack_ids={smaller_pack.pack_id},
    )

    assert not fallback.use_llm
    assert fallback.backend is RuntimeBackend.RULES
    assert packs.ensure_ids == []


def test_gpu_runtime_fallback_never_degrades_to_cpu_placement():
    gpu_pack = model_pack()
    cpu_pack = replace(
        model_pack(),
        pack_id="gemma4-e2b-qat-v1",
        display_name="Gemma 4 E2B QAT",
        model="gemma4:e2b-it-qat",
        parameters="2.3B effective",
        size_bytes=5 * GIB,
        min_ram_bytes=8 * GIB,
        min_vram_bytes=0,
        supported_backends=(RuntimeBackend.CPU,),
        installed=True,
    )

    class Packs:
        def __init__(self):
            self.ensure_ids = []

        def catalog(self, *, refresh_installed=True):
            return (gpu_pack, cpu_pack)

        def ensure(self, plan, *, consent, progress=None):
            self.ensure_ids.append(plan.pack_id)
            return cpu_pack

    packs = Packs()
    classifier = FakeClassifier()
    value = PolishEngine(
        rules=FakeRules(),
        classifier=classifier,
        packs=packs,
        runtime=FakeRuntime(()),
        hardware_provider=hardware,
        benchmark_cache=PolishBenchmarkCache(MemoryStore()),
    )
    decision = classifier.classify(
        "The reports is not ready.",
        "The reports is not ready.",
        task=PolishTask.PROOFREAD,
    )

    fallback = value._accept_smaller_plan(
        request=request("The reports is not ready."),
        classifier=decision,
        snapshot=hardware(),
        rejected_plan=probe_plan(gpu_pack),
        attempted_pack_ids={gpu_pack.pack_id},
    )

    assert not fallback.use_llm
    assert fallback.backend is RuntimeBackend.RULES
    assert packs.ensure_ids == []


def test_true_cancellation_does_not_activate_a_fallback_pack():
    larger_pack = model_pack()
    smaller_pack = replace(
        model_pack(),
        pack_id="gemma4-e2b-qat-v1",
        display_name="Gemma 4 E2B QAT",
        model="gemma4:e2b-it-qat",
        parameters="2.3B effective",
        size_bytes=5 * GIB,
        min_ram_bytes=8 * GIB,
        min_vram_bytes=6 * GIB,
        installed=True,
    )

    class Packs:
        def __init__(self):
            self.ensure_ids = []

        def catalog(self, *, refresh_installed=True):
            return (larger_pack, smaller_pack)

        def ensure(self, plan, *, consent, progress=None):
            self.ensure_ids.append(plan.pack_id)
            return larger_pack if plan.pack_id == larger_pack.pack_id else smaller_pack

    class Runtime(FakeRuntime):
        def generate(self, generation_request):
            self.generate_calls.append(generation_request)
            raise RuntimeError("operation cancelled by user")

    packs = Packs()
    value = PolishEngine(
        rules=FakeRules(),
        classifier=FakeClassifier(),
        packs=packs,
        runtime=Runtime(()),
        hardware_provider=hardware,
        benchmark_cache=PolishBenchmarkCache(MemoryStore()),
    )

    with pytest.raises(RuntimeError, match="cancelled"):
        value.analyze(request("The reports is not ready."))

    assert packs.ensure_ids == [larger_pack.pack_id]


def test_all_poisoned_next_request_preserves_latest_failure_kind_and_message():
    larger_pack = model_pack()
    smaller_pack = replace(
        model_pack(),
        pack_id="gemma4-e2b-qat-v1",
        display_name="Gemma 4 E2B QAT",
        model="gemma4:e2b-it-qat",
        parameters="2.3B effective",
        size_bytes=5 * GIB,
        min_ram_bytes=8 * GIB,
        min_vram_bytes=6 * GIB,
        installed=True,
    )

    class Packs:
        def catalog(self, *, refresh_installed=True):
            return (larger_pack, smaller_pack)

        def ensure(self, plan, *, consent, progress=None):
            return larger_pack if plan.pack_id == larger_pack.pack_id else smaller_pack

    class Runtime(FakeRuntime):
        def generate(self, generation_request):
            self.generate_calls.append(generation_request)
            if generation_request.plan.pack_id == larger_pack.pack_id:
                raise RuntimeError("primary GPU failed")
            raise RuntimeError("final generation timed out")

    value = PolishEngine(
        rules=FakeRules(),
        classifier=FakeClassifier(),
        packs=Packs(),
        runtime=Runtime(()),
        hardware_provider=hardware,
        benchmark_cache=PolishBenchmarkCache(MemoryStore()),
    )

    first = value.analyze(request("The reports is not ready."))
    second = value.analyze(request("The reports is not ready."))

    assert first.runtime_failure_kind is RuntimeFailureKind.GENERATION_TIMEOUT
    assert "final generation timed out" in first.message
    assert second.runtime_failure_kind is RuntimeFailureKind.GENERATION_TIMEOUT
    assert "final generation timed out" in second.message


def test_recovery_generation_failure_uses_installed_smaller_pack():
    larger_pack = model_pack()
    smaller_pack = replace(
        model_pack(),
        pack_id="gemma4-e2b-qat-v1",
        display_name="Gemma 4 E2B QAT",
        model="gemma4:e2b-it-qat",
        parameters="2.3B effective",
        size_bytes=5 * GIB,
        min_ram_bytes=8 * GIB,
        min_vram_bytes=6 * GIB,
        installed=True,
    )

    class Packs:
        def __init__(self):
            self.ensure_ids = []

        def catalog(self, *, refresh_installed=True):
            return (larger_pack, smaller_pack)

        def ensure(self, plan, *, consent, progress=None):
            self.ensure_ids.append(plan.pack_id)
            if plan.pack_id == smaller_pack.pack_id:
                assert not consent
                return smaller_pack
            return larger_pack

    class RecoveryRuntime(FakeRuntime):
        def __init__(self):
            super().__init__(())
            self.primary_calls = 0
            self.generate_ids = []
            self.probe_ids = []

        def acceptance_probe(self, plan):
            self.probe_ids.append(plan.pack_id)
            return AcceptanceProbeResult(
                accepted=True,
                elapsed_seconds=0.5,
                correctness_passed=True,
                protected_passed=True,
                backend=RuntimeBackend.CUDA,
            )

        def generate(self, generation_request):
            self.generate_calls.append(generation_request)
            self.generate_ids.append(generation_request.plan.pack_id)
            if generation_request.plan.pack_id == larger_pack.pack_id:
                self.primary_calls += 1
                if self.primary_calls <= 2:
                    return GenerationResult(
                        "The build 48 is ready.",
                        generation_request.plan.model,
                        RuntimeBackend.CUDA,
                        0.25,
                    )
                raise RuntimeError("GPU failed during recovery")
            return GenerationResult(
                "The reports are not ready.",
                generation_request.plan.model,
                RuntimeBackend.CUDA,
                0.25,
            )

    packs = Packs()
    runtime = RecoveryRuntime()
    value = PolishEngine(
        rules=FakeRules(),
        classifier=FakeClassifier(),
        packs=packs,
        runtime=runtime,
        hardware_provider=hardware,
        benchmark_cache=PolishBenchmarkCache(MemoryStore()),
    )

    result = value.analyze(
        replace(request("Teh reports is not ready."), download_consent=True)
    )

    assert result.used_llm
    assert result.review_ready
    assert result.text == "The reports are not ready."
    assert result.plan.pack_id == smaller_pack.pack_id
    assert "GPU failed during recovery" in result.plan.downgrade_reason
    assert packs.ensure_ids == [larger_pack.pack_id, smaller_pack.pack_id]
    assert runtime.probe_ids == [larger_pack.pack_id, smaller_pack.pack_id]
    assert runtime.generate_ids == [
        larger_pack.pack_id,
        larger_pack.pack_id,
        larger_pack.pack_id,
        smaller_pack.pack_id,
    ]


def test_all_failed_installed_packs_leave_ai_disabled():
    pack_e4b = replace(
        model_pack(),
        pack_id="gemma4-e4b-qat-v1",
        display_name="Gemma 4 E4B QAT",
        model="gemma4:e4b-it-qat",
        parameters="4.5B effective",
        quantization="Q4_0",
        size_bytes=6 * GIB,
        min_vram_bytes=8 * GIB,
    )
    pack_e2b = replace(
        model_pack(),
        pack_id="gemma4-e2b-qat-v1",
        display_name="Gemma 4 E2B QAT",
        model="gemma4:e2b-it-qat",
        parameters="2.3B effective",
        quantization="Q4_0",
        size_bytes=5 * GIB,
        min_vram_bytes=6 * GIB,
    )

    class CatalogPacks:
        def catalog(self, *, refresh_installed=True):
            return (pack_e4b, pack_e2b)

    store = MemoryStore()
    cache = PolishBenchmarkCache(store)
    snapshot = hardware()
    runtime = FakeRuntime(())
    for pack, record in (
        (
            pack_e4b,
            BenchmarkRecord(
                accepted=False,
                correctness_passed=True,
                protected_passed=True,
                elapsed_seconds=6.3,
                backend="cuda",
                reason="warm latency exceeded 5 seconds",
                measured_at=datetime.now(timezone.utc).isoformat(),
            ),
        ),
        (
            pack_e2b,
            BenchmarkRecord(
                accepted=False,
                correctness_passed=False,
                protected_passed=False,
                elapsed_seconds=2.0,
                backend="cuda",
                reason="protected content changed",
                measured_at=datetime.now(timezone.utc).isoformat(),
            ),
        ),
    ):
        cache.put(
            cache.key(
                snapshot,
                runtime_id=runtime.runtime_id,
                pack=pack,
                plan=probe_plan(pack),
            ),
            record,
        )

    value = PolishEngine(
        rules=FakeRules(),
        classifier=FakeClassifier(),
        packs=CatalogPacks(),
        runtime=runtime,
        hardware_provider=lambda: snapshot,
        benchmark_cache=cache,
    )
    plan = probe_plan(pack_e2b)

    accepted = value._accept_plan(plan, snapshot, pack_e2b)

    assert not accepted.use_llm
    assert accepted.pack_id == ""
    assert accepted.backend is RuntimeBackend.RULES
    assert "failed its local acceptance probe" in accepted.downgrade_reason


def test_failed_acceptance_gate_falls_back_to_next_installed_pack():
    preferred_pack = replace(
        model_pack(),
        pack_id="gemma4-12b-q4-k-m-v1",
        display_name="Gemma 4 12B Q4_K_M",
        model="gemma4:12b-it-q4_K_M",
        family="Gemma 4",
        parameters="12B",
        size_bytes=8 * GIB,
    )
    next_pack = replace(
        model_pack(),
        pack_id="gemma4-e4b-qat-v1",
        display_name="Gemma 4 E4B QAT",
        model="gemma4:e4b-it-qat",
        family="Gemma 4",
        parameters="4.5B effective",
        quantization="Q4_0",
        size_bytes=6 * GIB,
        min_vram_bytes=8 * GIB,
    )

    class CatalogPacks:
        def catalog(self, *, refresh_installed=True):
            return (preferred_pack, next_pack)

    class FalsePositiveProbeRuntime:
        def acceptance_probe(self, plan):
            correctness_passed = plan.pack_id == next_pack.pack_id
            return AcceptanceProbeResult(
                accepted=True,
                elapsed_seconds=0.5,
                correctness_passed=correctness_passed,
                protected_passed=True,
                backend=RuntimeBackend.CUDA,
                model_identity=plan.model,
            )

    runtime = AdaptiveInferenceRuntime(fallback=FalsePositiveProbeRuntime())
    snapshot = hardware()
    value = PolishEngine(
        rules=FakeRules(),
        classifier=FakeClassifier(),
        packs=CatalogPacks(),
        runtime=runtime,
        hardware_provider=lambda: snapshot,
        benchmark_cache=PolishBenchmarkCache(MemoryStore()),
    )
    initial_plan = ExecutionPlan(
        policy_version=1,
        mode=PolishMode.BALANCED,
        task=PolishTask.PROOFREAD,
        use_llm=True,
        pack_id=preferred_pack.pack_id,
        model=preferred_pack.model,
        backend=RuntimeBackend.CUDA,
        context_window=2048,
        max_output_tokens=128,
        warm_latency_limit_seconds=5.0,
        require_full_gpu=True,
    )

    accepted = value._accept_plan(initial_plan, snapshot, preferred_pack)

    assert accepted.use_llm
    assert accepted.pack_id == next_pack.pack_id
    assert accepted.model == next_pack.model


def test_benchmark_cache_never_contains_user_text():
    text = "PRIVATE_BENCHMARK_TEXT_MUST_NOT_BE_STORED"
    runtime = FakeRuntime((text,))
    value, store = engine(classifier=FakeClassifier(), runtime=runtime)
    value.analyze(
        CorrectionRequest(
            text,
            task=PolishTask.REWRITE,
            mode=PolishMode.BALANCED,
            command="Rewrite",
            manual=True,
        )
    )
    assert text not in repr(store.values.get("polish_benchmark_cache"))

def test_empathetic_adapter_keeps_people_centered_ai_proofread_after_unsafe_style():
    class RecordingEngine:
        def analyze(self, value):
            proofread = value.command == "Proofread"
            output = (
                "Nobody could understand which screen the trainers were describing, "
                "and the caption file stopped after nineteen minutes."
                if proofread
                else "Everyone felt devastated and deserved an apology."
            )
            return CorrectionResult(
                original_text=value.text,
                normalized_text=value.text,
                deterministic_text=value.text,
                text=output,
                classifier=ClassifierDecision(
                    needs_llm=True,
                    error_categories=(),
                    severity=1.0,
                    context_scope=ContextScope.SENTENCE,
                    english_confidence=1.0,
                    backend="test",
                    elapsed_ms=1.0,
                ),
                plan=ExecutionPlan(
                    policy_version=1,
                    mode=PolishMode.BALANCED,
                    task=value.task,
                    use_llm=True,
                    model="qwen3:8b",
                    backend=RuntimeBackend.CUDA,
                ),
                validation=ValidationResult(
                    proofread,
                    1.0,
                    () if proofread else ("invented feelings",),
                ),
                used_llm=proofread,
                rejected_llm=not proofread,
                quality_complete=proofread,
                tone_verified=proofread,
                quality_score=1.0 if proofread else 0.0,
                generation_seconds=1.0,
                review_ready=proofread,
            )

    adapter = PolishWritingAssistantAdapter(
        RecordingEngine(),
        mode_provider=lambda: PolishMode.BALANCED,
        consent_provider=lambda: True,
    )

    result = adapter.run(
        "Nobody could understand which screen they was describing.",
        command="Empathetic",
    )

    assert result.text == (
        "Nobody could understand which screen the trainers were describing, "
        "and the caption file stopped after nineteen minutes."
    )
    correction = adapter.last_correction_result
    assert correction.quality_complete
    assert correction.tone_verified
    assert correction.review_ready
    assert correction.rejected_llm
    assert correction.residual_errors == ()
    assert "Empathetic impact already verified" in correction.message


def test_persuasive_adapter_restructures_verified_factual_ai_proofread():
    proofread_text = (
        "The researchers corrected that problem on April 22 by adding translated "
        "forms and hiring Linh Tran and Mateo Silva as paid outreach coordinators."
    )

    class RecordingEngine:
        def analyze(self, value):
            succeeded = value.command == "Proofread"
            return CorrectionResult(
                original_text=value.text,
                normalized_text=value.text,
                deterministic_text=value.text,
                text=proofread_text,
                classifier=ClassifierDecision(
                    needs_llm=True,
                    error_categories=(),
                    severity=1.0,
                    context_scope=ContextScope.SENTENCE,
                    english_confidence=1.0,
                    backend="test",
                    elapsed_ms=1.0,
                ),
                plan=ExecutionPlan(
                    policy_version=1,
                    mode=PolishMode.BALANCED,
                    task=value.task,
                    use_llm=True,
                    model="qwen3:8b",
                    backend=RuntimeBackend.CUDA,
                ),
                validation=ValidationResult(succeeded, 1.0, ()),
                used_llm=succeeded,
                rejected_llm=not succeeded,
                quality_complete=succeeded,
                tone_verified=succeeded,
                quality_score=1.0 if succeeded else 0.0,
                generation_seconds=1.0,
                review_ready=succeeded,
                residual_errors=(
                    ()
                    if succeeded
                    else ("persuasive rewrite is too close to the source",)
                ),
            )

    adapter = PolishWritingAssistantAdapter(
        RecordingEngine(),
        mode_provider=lambda: PolishMode.BALANCED,
        consent_provider=lambda: True,
    )

    result = adapter.run(proofread_text, command="Persuasive")

    assert result.text == (
        "By adding translated forms and hiring Linh Tran and Mateo Silva as paid "
        "outreach coordinators, the researchers corrected that problem on April 22."
    )
    correction = adapter.last_correction_result
    assert correction.quality_complete
    assert correction.tone_verified
    assert correction.review_ready
    assert correction.rejected_llm
    assert correction.residual_errors == ()
    assert "safe Persuasive restructure" in correction.message


def test_a_refused_correction_does_not_take_the_pack_out_of_service() -> None:
    """One rejected paragraph was disabling the AI for every paragraph after it.

    A failed pack is held out of service for five minutes so a genuinely broken
    runtime is not retried on every section. That was applied to validation
    failures too - cases where the model answered and the answer was refused
    for this particular text. Measured on the essay corpus: paragraph 5 of
    essay 1 dropped a protected token, the pack was blacklisted, and paragraphs
    6 through 10 returned in a tenth of a second each with "full AI correction
    did not run". Across twenty essays the local AI reached three paragraphs
    out of two hundred, and nothing told the reader.

    The record still has to outlive the request that made it, because the
    cascade to a smaller pack reads it moments later, so it expires in seconds
    rather than never being written.
    """
    from echo_link_core.polish import (
        PolishEngine,
        _FAILED_PACK_TTL_SECONDS,
        _VALIDATION_FAILURE_TTL_SECONDS,
    )
    from echo_link_core.polish_types import RuntimeFailureKind

    engine = PolishEngine()
    clock = [1000.0]
    engine._monotonic = lambda: clock[0]

    engine._remember_failed_pack(
        "gemma4-e2b-qat-v1",
        "The result changed protected content.",
        kind=RuntimeFailureKind.VALIDATION_FAILURE,
    )
    # Still recorded, so this request can cascade to a smaller pack.
    assert "gemma4-e2b-qat-v1" in engine._failed_pack_ids()

    # Gone before the next paragraph is sent. The window is shorter than one
    # rules-only paragraph on purpose: at five seconds one refusal still
    # covered the next ten paragraphs, and one of those re-armed it.
    clock[0] += _VALIDATION_FAILURE_TTL_SECONDS + 0.01
    assert engine._failed_pack_ids() == set()

    # A real runtime fault still holds the pack out for the full window.
    engine._remember_failed_pack(
        "gemma4-e2b-qat-v1",
        "CUDA device was lost.",
        kind=RuntimeFailureKind.GPU_FAILURE,
    )
    clock[0] += _VALIDATION_FAILURE_TTL_SECONDS + 0.01
    assert "gemma4-e2b-qat-v1" in engine._failed_pack_ids()
    clock[0] += _FAILED_PACK_TTL_SECONDS
    assert engine._failed_pack_ids() == set()


def test_a_broken_model_manifest_is_reported_not_swallowed(caplog):
    """Losing the catalogue turns the local AI off, so it must be visible.

    A manifest was once edited without being re-signed, and every pack failed
    its signature check. The engine fell back to deterministic rules and said
    nothing at all - no log line, no status text - so the product's main
    feature was off for a whole session and the only symptom was writing that
    was quietly worse.
    """
    import logging

    from echo_link_core.polish import PolishEngine
    from echo_link_core.polish_model_packs import ModelPackSecurityError

    class BrokenPacks:
        def catalog(self, refresh_installed=False):
            raise ModelPackSecurityError("Polish model manifest signature failed.")

    engine = PolishEngine(packs=BrokenPacks())
    with caplog.at_level(logging.WARNING, logger="echo_link_core.polish"):
        first_catalog, first_reason = engine._catalog()
        engine._catalog()

    assert first_catalog == ()
    assert "signature failed" in first_reason
    warnings = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING
        and "rules only" in record.getMessage()
    ]
    # Reported, and reported once rather than on every paragraph.
    assert len(warnings) == 1
    assert "signature failed" in warnings[0].getMessage()


def _validator():
    from echo_link_core.polish import PolishValidator
    from echo_link_core.polish_classifier import MiniLMPolishClassifier

    return PolishValidator(MiniLMPolishClassifier())


def test_repaired_modal_typo_is_not_an_invented_modal():
    """A misspelled modal the model spells correctly is not a meaning change.

    "cud work real good" -> "could work really well" was refused as "modal
    meaning changed (added could)". The speller declines "cud" on purpose - it
    is a real word - so no dictionary can resolve it. The candidate is the
    evidence instead.
    """
    from echo_link_core.polish import _reconcile_guard_vocabulary

    source = "and why keepin one for wallking afer school cud work real good"
    candidate = "And why keeping one for walking after school could work really well."
    reconciled = _reconcile_guard_vocabulary(source, candidate)
    assert "could" in reconciled
    assert "after" in reconciled


def test_guard_vocabulary_is_not_invented_when_the_candidate_never_uses_it():
    """The candidate has to name the target, or nothing is rewritten.

    An earlier version rewrote the source alone and turned "wether" into
    "neither" and "lights" into "might", inventing the very negation and modal
    it was meant to be checking.
    """
    from echo_link_core.polish import _reconcile_guard_vocabulary

    source = "bath has its own wether streest noises and some habitats need lights"
    candidate = "Bath has its own weather, streets, noises, and some habitats need lights."
    assert _reconcile_guard_vocabulary(source, candidate) == source


def test_split_word_negation_is_joined_before_counting():
    """"with out proper care" is "without proper care", not a new negation."""
    from echo_link_core.polish import _negation_count, _normalize_contractions

    source = _normalize_contractions("with out proper cair the animal suffers")
    candidate = _normalize_contractions("without proper care the animal suffers")
    assert _negation_count(source) == _negation_count(candidate)


def test_misspelled_function_word_is_not_a_named_entity():
    """Correcting "Thier is" must not read as removing a name.

    _named_entities reads any capitalized run as a name, so a capitalized typo
    is one, and repairing it refused the whole paragraph.
    """
    from echo_link_core.polish import _named_entities

    entities = _named_entities("Thier is emotional benefits as well and the dog waits.")
    assert "Thier" not in entities
    # A real name in the same position is still a name.
    assert "Bakersfield" in _named_entities("Bakersfield is dusty in the summer.")


def test_dropping_a_discourse_hedge_is_not_an_actor_change():
    """"important in i guess every country" -> "important in every country".

    The pronoun inside filler counted as an actor, so deleting the filler was
    refused as "actor changed (missing i)".
    """
    from echo_link_core.polish import _actors_preserved

    assert _actors_preserved(
        "companionship is important in i guess every country",
        "Companionship is important in every country.",
        allow_redundant_pronoun=True,
    )
    # A real first-person actor still cannot be dropped.
    assert not _actors_preserved(
        "i walked the dog before school",
        "The dog was walked before school.",
        allow_redundant_pronoun=True,
    )


def test_repaired_typo_paragraph_is_accepted_end_to_end():
    """The guards together accept a correct repair of badly misspelled text."""
    validator = _validator()
    source = (
        "The OWNER in Bakersfield cud wriite a chhart ON the wall , set alarms "
        "or ask a famly member to help and if nobody does the chores the pett "
        "suffers, whicch IS bad."
    )
    candidate = (
        "The owner in Bakersfield could write a chart on the wall, set alarms, "
        "or ask a family member to help, and if nobody does the chores, the pet "
        "suffers, which is bad."
    )
    result = validator.validate(
        source,
        candidate,
        task=PolishTask.PROOFREAD,
        grammar_mode=True,
    )
    assert result.accepted, result.failures


def test_a_refused_correction_records_why_in_engine_terms():
    """The reader-facing message hides the cause; the metrics must not.

    Nine rules-only paragraphs were reported as nine successes because nothing
    outside the engine could tell a refused section from a clean one.
    """
    from echo_link_core.polish import PolishValidator

    engine = PolishEngine()

    class RefusingValidator(PolishValidator):
        def validate(self, source, candidate, **kwargs):
            from echo_link_core.polish_types import ValidationResult

            if candidate.strip() != source.strip():
                return ValidationResult(
                    accepted=False,
                    semantic_similarity=1.0,
                    failures=("negation changed (test)",),
                )
            return super().validate(source, candidate, **kwargs)

    engine.validator = RefusingValidator(engine.classifier)
    result = engine.analyze(
        CorrectionRequest(
            text="teh dog dont fit the situashun.",
            task=PolishTask.PROOFREAD,
            mode=PolishMode("balanced"),
            command="Proofread",
            download_consent=True,
            manual=True,
        )
    )
    assert not result.used_llm
    assert result.metrics.get("refusal_reason")


def test_dropping_a_redundant_it_subject_is_allowed_but_not_a_fragment():
    """"and also it shows" -> "and also shows" is the tidy-up a proofread is for.

    The mirror of the allowance for adding "it", which has been in place since
    the fragment repair went in. "it" names nobody, so dropping it cannot lose
    who acts - but it can leave a fragment, so some actor has to remain.
    """
    from echo_link_core.polish import _actors_preserved

    assert _actors_preserved(
        "and also it shows the pet is theirs",
        "and also shows the pet is theirs",
        allow_redundant_pronoun=True,
    )
    # Nothing is left to act, so this is a rewrite, not a tidy-up.
    assert not _actors_preserved(
        "which is why it is good but also not.",
        "This is good but also not.",
        allow_redundant_pronoun=True,
    )
    # A swap is still a swap.
    assert not _actors_preserved(
        "he closed the ticket",
        "she closed the ticket",
        allow_redundant_pronoun=True,
    )


def test_a_repeated_modal_may_be_reduced_but_not_lost():
    """Multiplicity is not meaning; presence is.

    "then maybe forgets their keys because the maybe morning got busy" loses the
    stray second one when repaired. Losing the family outright is different: the
    model dropping "its needs is maybe met" to "its needs are met" turns a hedge
    into a claim, and that still fails.
    """
    from echo_link_core.polish import _modal_meaning_preserved

    assert _modal_meaning_preserved(
        "then may forgets their keys because the may morning got busy",
        "then may forget their keys because the morning got busy",
        grammar_mode=True,
    )
    assert not _modal_meaning_preserved(
        "a pet can only be great when its needs is may met",
        "a pet can only be great when its needs are met",
        grammar_mode=True,
    )


def test_guard_reconciliation_never_breaks_a_contraction():
    """"shouldn't" is one word, not "shouldn" plus a stray "t".

    Splitting on letters alone made "shouldn" a token one edit from "should",
    so the source was rewritten to "should't" - which deleted the negation the
    guard was about to count and refused a correct repair for reversing the
    writer's meaning. A word carrying an apostrophe is never rewritten.
    """
    from echo_link_core.polish import (
        _negation_count,
        _normalize_contractions,
        _reconcile_guard_vocabulary,
    )

    source = _normalize_contractions("They shouldnt chew shoes.")
    candidate = _normalize_contractions("They should not chew shoes.")
    reconciled = _reconcile_guard_vocabulary(source, candidate)
    assert "should't" not in reconciled
    assert _negation_count(reconciled) == _negation_count(candidate)
    # The typo repair it exists for still works.
    assert "could" in _reconcile_guard_vocabulary(
        "the backyard cud work real good",
        "the backyard could work really well",
    )


def test_three_letter_function_word_typo_is_not_a_protected_name():
    """"Wen somebody says" opens essay 1 with a typo, not with a person.

    A four-character floor in _misspelled_non_entity_starter let "Wen" through
    as a proper name, so it became an opaque immutable token. The model then
    correctly wrote "When", the token was gone, and the whole 627-character
    section was refused as changed protected content and fell back to the
    deterministic rules with every typo in it intact.
    """
    from echo_link_core.polish import (
        _misspelled_non_entity_starter,
        _runtime_opaque_entities,
    )

    assert _misspelled_non_entity_starter("wen") is True

    paragraph = (
        "Wen somebody says A certain animul cant be a pett they usually is "
        "speaking befor they lernt much bout it and in Bakersfield , United "
        "States to be honest this this can be especialy true for a DOG."
    )
    entities = _runtime_opaque_entities(paragraph)
    assert "Wen" not in entities
    # The real place name in the same sentence is still protected, because the
    # guard this narrows is the one that stops Malik becoming Mali.
    assert "Bakersfield" in entities


def test_short_real_names_are_still_protected_entities():
    """Two edits is far too loose at three letters; one deletion is not.

    "Ann" is a single substitution from "and", so any rule that merely lowered
    the length floor would have stopped treating it as a name. Only a letter
    dropped from the function word counts, and no deletion from "and" spells
    "ann".
    """
    from echo_link_core.polish import _misspelled_non_entity_starter

    for name in ("ann", "tom", "ian", "bob", "joe", "sue", "kim", "mia", "amy"):
        assert _misspelled_non_entity_starter(name) is False, name


def test_a_full_stop_followed_by_a_comma_is_repaired():
    """"same shirt again., but it mainly wants" is punctuation nobody typed.

    The writer's source ran two sentences together with a full stop; the model
    rewrote the join as a comma clause and the original full stop survived
    beside it, so the product damaged text it had otherwise corrected well.
    """
    from echo_link_core.selected_text import _repair_stranded_terminator

    assert _repair_stranded_terminator(
        "wearing the same shirt again., but it mainly wants food."
    ) == "wearing the same shirt again, but it mainly wants food."
    assert _repair_stranded_terminator("He left. , then returned.") == (
        "He left, then returned."
    )


def test_abbreviations_keep_their_full_stop_before_a_comma():
    """"etc., and" and "Mr. Smith, who" are correct English and must survive."""
    from echo_link_core.selected_text import _repair_stranded_terminator

    for value in (
        "lights, heaters, filters, etc., and other equipment.",
        "Bring a leash, e.g., a short one.",
        "Ask Dr. Reyes, who runs the clinic.",
    ):
        assert _repair_stranded_terminator(value) == value, value


def test_clock_abbreviations_keep_their_full_stop():
    """"At 12:40 p.m., observers will test the alarm" is correct as written.

    The full suite caught this: the first version of the repair turned
    "p.m.," into "p.m,". A regression test, not a comment, keeps it caught.
    """
    from echo_link_core.selected_text import _repair_stranded_terminator

    for value in (
        "At 12:40 p.m., observers will test the alarm.",
        "At 9:05 a.m., the vet arrives.",
    ):
        assert _repair_stranded_terminator(value) == value, value
