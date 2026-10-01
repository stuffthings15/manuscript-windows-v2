"""A tone request must ask the model for the tone before anything else.

Found 2026-09-30. Every Friendly answer the HUD produced read as a cold
proofread: no contractions, "cannot", "do not", and the warmth check refused
all of them. The retry ran; it got the same cold answer back. The cause was
the prompt. The generation instruction for a tone opened with the grammar
checklist written for Proofread, and the tone contract sat at the end of it,
so the model did the dominant task and expanded every contraction it found.
"""

from __future__ import annotations

from echo_link_core.polish import _generation_instruction
from echo_link_core.polish_types import (
    ClassifierDecision,
    ContextScope,
    CorrectionRequest,
    PolishMode,
    PolishTask,
)


def _classifier() -> ClassifierDecision:
    return ClassifierDecision(
        needs_llm=True,
        error_categories=("grammar",),
        severity=0.4,
        context_scope=ContextScope.PARAGRAPH,
        english_confidence=0.99,
        backend="rules",
        elapsed_ms=1.0,
    )


def _request(command: str, task: PolishTask) -> CorrectionRequest:
    return CorrectionRequest(
        text="Kids likes dogs. They dont bite.",
        task=task,
        mode=PolishMode.AUTO,
        command=command,
        manual=True,
    )


def test_a_tone_instruction_opens_with_the_tone_not_the_grammar_checklist():
    instruction = _generation_instruction(_request("Friendly", PolishTask.TONE), _classifier())
    tone_at = instruction.find("Tone contract:")
    grammar_at = instruction.find("subject-verb agreement")
    assert tone_at >= 0
    assert instruction.startswith("Primary task: rewrite the text in the Friendly tone")
    # The grammar checklist is Proofread's. On an already-proofread tone
    # request it only tells the model to do a second proofread.
    assert grammar_at == -1 or grammar_at > tone_at


def test_a_warm_tone_is_told_to_keep_contractions():
    instruction = _generation_instruction(_request("Friendly", PolishTask.TONE), _classifier())
    # Told to contract what it is given, with the pairs named: the proofread
    # stage writes "does not" and the model kept it when only told not to
    # expand (Word, 2026-09-30, one batch in four verified warm).
    assert "Contract every full form" in instruction
    assert "doesn't" in instruction and "does not" in instruction


def test_a_formal_tone_is_not_told_to_use_contractions():
    instruction = _generation_instruction(_request("Formal", PolishTask.TONE), _classifier())
    assert "Contract every full form" not in instruction
    assert instruction.startswith("Primary task: rewrite the text in the Formal tone")


def test_a_proofread_keeps_the_grammar_checklist_first():
    instruction = _generation_instruction(
        _request("Proofread", PolishTask.PROOFREAD), _classifier()
    )
    assert instruction.startswith("Polish scope:")
    assert "subject-verb agreement" in instruction
    assert "Primary task: rewrite" not in instruction


def test_a_tone_still_carries_the_safety_constraints():
    instruction = _generation_instruction(_request("Friendly", PolishTask.TONE), _classifier())
    for phrase in (
        "named entities must remain exact",
        "Never use an em dash",
        "Do not add a named entity or fact",
        "Thinking must remain disabled",
    ):
        assert phrase in instruction
