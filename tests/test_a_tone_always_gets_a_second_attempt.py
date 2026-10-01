"""A tone request must get more than one attempt at being the tone.

Curtis, 2026-09-30, after reading a corrected document: "It did the
correction, but then it did not rewrite in the Proper tone."

He was right, and the counters said so on all seven tones measured in Word
that day:

    ran={'Proofread': 10, 'Friendly': 4}
    residual='friendly rewrite is not visibly warm'

The ORDER was never wrong. Correct, then tone, then score - all three happen.
The scoring runs and FAILS, and the answer is applied anyway.

That it is applied is deliberate and stays. _beats_deterministic prefers the
model's unverified tone to the rules repair, because the repair is a proofread
with no warmth at all; forcing it scored Friendly 7.1 percent on 2026-08-20.

What was missing is the attempt before it. _beats_deterministic's own comment
says "the retry added in 507afdb still runs first", and on this path it did
not: an automatic HUD tone request is not `manual`, and AUTO gives
retry_limit 0 below severity 0.75, so attempt_count was 1. One answer, not
warm, accepted.

Grammar has a second source of a correct answer; a tone does not, because the
fallback cannot produce a tone at all. Its attempts are the only chance the
tone ever gets.
"""
from __future__ import annotations

import pytest

from echo_link_core.polish_hardware import HardwarePolicyManager
from echo_link_core.polish_types import PolishMode, PolishTask, RuntimeBackend

from test_polish_hardware import PACKS, decision, snapshot


SNAPSHOT = dict(ram=64, vram=16, backend=RuntimeBackend.CUDA)


def _plan(task, mode, *, severity=0.1, manual=False):
    return HardwarePolicyManager().plan(
        snapshot(**SNAPSHOT),
        task,
        mode,
        classifier=decision(severity=severity),
        packs=PACKS,
        manual=manual,
    )


@pytest.mark.parametrize(
    "mode", [PolishMode.AUTO, PolishMode.BALANCED, PolishMode.PROFESSIONAL]
)
def test_a_tone_gets_a_retry_even_unasked_and_at_low_severity(mode):
    """The reported defect. AUTO at severity 0.1, not manual, gave zero."""
    assert _plan(PolishTask.TONE, mode).retry_limit >= 1


def test_the_tone_retry_does_not_apply_to_instant():
    """Instant is a latency promise; a second generation would break it.

    Mirrors the manual-retry floor directly above it, which excludes INSTANT
    for the same reason.
    """
    plan = _plan(PolishTask.TONE, PolishMode.INSTANT)
    assert plan.retry_limit == 0


def test_an_unasked_low_severity_proofread_still_gets_none():
    """The floor is for tones, not a blanket extra generation everywhere.

    A proofread has the deterministic repair behind it, which is a real second
    source of a correct answer, so it keeps the cheaper policy.
    """
    assert _plan(PolishTask.PROOFREAD, PolishMode.AUTO).retry_limit == 0


def test_a_manual_proofread_keeps_its_own_retry():
    """507afdb's floor must survive this change."""
    assert _plan(PolishTask.PROOFREAD, PolishMode.AUTO, manual=True).retry_limit >= 1
