"""Where the text on a review card came from, decided in one place.

The owner corrected a sentence about ducks being funny pets and the grammar was
never repaired, because the card offered the rules repair as acceptable text
while the model was still working on that section.

Before this, two surfaces worked out provenance separately - the card from its
own set of completed ids, the window from `_polish_result_ready_for_review` -
so they could disagree, and did. Worse, both collapsed "the AI has not answered
yet" and "the AI answered and I refused it" into one state, and offered both as
text the writer could take.

These tests hold the four states apart, because the product behaviour differs
for every one of them.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from echo_link_core.polish_readiness import (  # noqa: E402
    AI,
    AI_PENDING,
    AI_REJECTED,
    AI_UNAVAILABLE,
    correction_provenance,
)
from echo_link_core.polish_types import RuntimeFailureKind  # noqa: E402


class _Validation:
    def __init__(self, accepted: bool) -> None:
        self.accepted = accepted


class _Result:
    def __init__(
        self,
        *,
        used_llm: bool = False,
        accepted: bool = False,
        quality_complete: bool = False,
        review_ready: bool = False,
        failure: RuntimeFailureKind = RuntimeFailureKind.NONE,
        text: str = "Ducks are funny pets.",
    ) -> None:
        self.used_llm = used_llm
        self.validation = _Validation(accepted)
        self.quality_complete = quality_complete
        self.review_ready = review_ready
        self.runtime_failure_kind = failure
        self.text = text


def test_a_model_answer_the_guards_accepted_is_ai():
    result = _Result(used_llm=True, accepted=True, quality_complete=True)

    assert correction_provenance(result) == AI


def test_no_result_yet_is_pending_not_acceptable_text():
    """The state the owner hit.

    The card is built in the same millisecond the review opens, while the model
    is still working. Nothing has come back, so there is nothing to accept -
    but the old code had no word for that and offered the rules repair.
    """
    assert correction_provenance(None) == AI_PENDING


def test_a_refused_model_answer_is_rejected_not_missing():
    """VALIDATION_FAILURE means the model answered and a guard turned it down.

    That is a different situation from no AI at all, and the writer should be
    told which one happened rather than handed the same card for both.
    """
    result = _Result(failure=RuntimeFailureKind.VALIDATION_FAILURE)

    assert correction_provenance(result) == AI_REJECTED


def test_a_review_with_the_ai_switched_off_is_unavailable():
    """Nothing is coming, so this must never report pending.

    A card that waits forever for an answer that was never requested is worse
    than one that says the rules are all there is.
    """
    result = _Result(used_llm=True, accepted=True, quality_complete=True)

    assert correction_provenance(result, ai_enabled=False) == AI_UNAVAILABLE
    assert correction_provenance(None, ai_enabled=False) == AI_UNAVAILABLE


def test_a_runtime_fault_is_unavailable_not_rejected():
    """No model, no GPU, dead connection: there was no AI stage to refuse."""
    result = _Result(failure=RuntimeFailureKind.GPU_FAILURE)

    assert correction_provenance(result) == AI_UNAVAILABLE


def test_an_unaccepted_answer_without_a_validation_failure_is_not_ai():
    """Whatever else it is, it is not the model's accepted work.

    The one thing this function must never do is call rules-only text AI,
    because that is what let a writer accept a sentence the model never saw.
    """
    result = _Result(used_llm=True, accepted=False)

    assert correction_provenance(result) != AI
