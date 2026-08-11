"""Did the AI deliver what the plan promised, and can the answer be shown?

These are decisions about an ExecutionPlan and a CorrectionResult, not about
widgets, so they belong in core even though the HUD is their only caller today.
The app package holds no correction logic of its own; see docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import logging

from .polish_types import (
    GPU_BACKENDS,
    PolishTask,
    RuntimeBackend,
    RuntimeFailureKind,
)


logger = logging.getLogger(__name__)


def _runtime_status_matches_plan(
    status,
    plan,
    *,
    expected_model: str,
    expected_backend: RuntimeBackend,
) -> bool:
    # Every branch below turns into the same sentence for the reader - the
    # local AI is off and the writing is being corrected by rules alone - so
    # each one says which check refused. Without this the app reported "no
    # room for the model" while the card was empty, and there was no way to
    # tell a wrong model name from a model sitting in system memory.
    actual_model = str(getattr(status, "model", "") or "").strip()
    actual_backend = getattr(status, "backend", RuntimeBackend.RULES)
    if not bool(getattr(status, "loaded", False)):
        logger.warning(
            "Local AI verification failed: runtime reports nothing loaded "
            "(wanted %s on %s)",
            expected_model,
            getattr(expected_backend, "value", expected_backend),
        )
        return False
    if actual_model != expected_model:
        logger.warning(
            "Local AI verification failed: runtime loaded %r, plan wanted %r",
            actual_model,
            expected_model,
        )
        return False
    if actual_backend is not expected_backend:
        logger.warning(
            "Local AI verification failed: %s loaded on %s, plan wanted %s",
            expected_model,
            getattr(actual_backend, "value", actual_backend),
            getattr(expected_backend, "value", expected_backend),
        )
        return False
    if not plan.require_full_gpu:
        return True
    model_bytes = max(0, int(getattr(status, "model_bytes", 0) or 0))
    gpu_bytes = max(0, int(getattr(status, "gpu_bytes", 0) or 0))
    if not (
        expected_backend in GPU_BACKENDS
        and model_bytes > 0
        and gpu_bytes >= model_bytes
    ):
        logger.warning(
            "Local AI verification failed: %s wants the whole model on the "
            "GPU but %.2f GB of %.2f GB is resident there",
            expected_model,
            gpu_bytes / (1024 ** 3),
            model_bytes / (1024 ** 3),
        )
        return False
    return True


def _polish_result_ready_for_review(result: object) -> bool:
    """Whether the HUD has a correction worth putting in front of the reader.

    An LLM answer that passed validation is obviously reviewable. So is the
    deterministic repair left behind when the LLM answer was *rejected*: the
    engine's fallback is the rules repair, with every spelling, agreement and
    punctuation fix already applied, and refusing it threw that work away.

    Requiring ``used_llm`` alone meant a section whose AI answer tripped a
    meaning guard produced no review card at all - the HUD sat on "analyzing"
    and the reader never saw the repairs that had been made. Three of ten
    sections in essay 1 were lost this way.

    A genuine runtime fault is different: no model, no GPU, a dead connection.
    Those keep the error path, because there is no AI stage to fall back from.
    """
    validation = getattr(result, "validation", None)
    if (
        getattr(result, "used_llm", False)
        and getattr(validation, "accepted", False)
        and (
            getattr(result, "quality_complete", False)
            or getattr(result, "review_ready", False)
        )
    ):
        return True
    # VALIDATION_FAILURE is set only when the model answered and the answer was
    # turned down, never when the runtime itself broke.
    return (
        getattr(result, "runtime_failure_kind", RuntimeFailureKind.NONE)
        is RuntimeFailureKind.VALIDATION_FAILURE
        and bool(str(getattr(result, "text", "")).strip())
    )


def _polish_task_for(command: str, text: str) -> PolishTask:
    if command == "Proofread":
        if len(text) > 1200 or len([part for part in text.split("\n\n") if part.strip()]) > 2:
            return PolishTask.DOCUMENT
        return PolishTask.PROOFREAD
    if command in {
        "Formal",
        "Friendly",
        "Professional",
        "Diplomatic",
        "Empathetic",
        "Cooperative",
        "Assertive",
        "Caveman",
    }:
        return PolishTask.TONE
    return PolishTask.REWRITE
