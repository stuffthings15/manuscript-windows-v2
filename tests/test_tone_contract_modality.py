"""The tone contract has to tell the model to keep the writer's modality.

Measured on 2026-08-20 in build/refusal-evidence-friendly.md: of 19 modal
refusals across three essays, 14 were the model ADDING EXACTLY ONE HEDGE and
removing none - "hesitation blocks useful progress" came back as "hesitation
CAN block useful progress", "making climate awareness part of responsible hive
management" as "climate awareness CAN be part of".

The guard is right to refuse those. A hedge weakens what the writer claimed,
and that is a change of meaning. `_restore_tone_modals` cannot rescue them
either: it only fires when both sides have the SAME number of modals, and
hedging changes the count by definition.

So the fix belongs in the instruction, not in the guard. This is the largest
single class of tone refusal and the only one whose repair weakens no safety
property.
"""
from __future__ import annotations

from echo_link_core.polish_quality import TONE_CONTRACTS, tone_contract_instruction


def test_every_tone_is_told_to_keep_the_writers_modality():
    for name in TONE_CONTRACTS:
        instruction = tone_contract_instruction(name).casefold()
        assert "modality" in instruction, name
        # Named explicitly, because "keep the modality" alone did not tell the
        # model which words it meant.
        for hedge in ("can", "might", "could"):
            assert hedge in instruction, (name, hedge)


def test_warmth_is_directed_somewhere_other_than_hedging():
    """Telling a model not to hedge, with no alternative, produces cold text.

    Friendly's failure was never that it hedged too little - usefulness was
    4.13 while style sat at 3.20. Removing its favourite warmth move without
    naming another leaves it with nothing, so the contract has to say where
    warmth is allowed to come from instead.
    """
    instruction = tone_contract_instruction("Friendly").casefold()

    assert "word choice" in instruction
