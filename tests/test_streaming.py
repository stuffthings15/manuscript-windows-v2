from __future__ import annotations

from echo_link_core.streaming import StablePartialAssembler


def test_stable_partial_assembler_commits_repeated_prefix_and_holds_tail():
    assembler = StablePartialAssembler(holdback_words=2)
    first = assembler.update("Hello this is a live preview.")
    assert first.stable_text == ""
    second = assembler.update("Hello this is a live preview.")
    assert second.stable_text == "Hello this is a"
    assert second.unstable_text == "live preview."
    third = assembler.update("Hello this is a better preview.")
    assert third.stable_text == "Hello this is a"
    assert third.display_text == "Hello this is a better preview."


def test_stable_partial_assembler_retracts_prefix_when_hypothesis_changes():
    assembler = StablePartialAssembler(holdback_words=1)
    assembler.update("Alpha beta gamma delta")
    assert assembler.update("Alpha beta gamma delta").stable_text == "Alpha beta gamma"
    changed = assembler.update("Alpha beta changed ending")
    assert changed.stable_text == "Alpha beta"
