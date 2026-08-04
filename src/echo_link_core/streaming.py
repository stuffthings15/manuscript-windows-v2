from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StableTranscript:
    stable_text: str
    unstable_text: str
    display_text: str


class StablePartialAssembler:
    """Commits only words repeated across consecutive local hypotheses."""

    def __init__(self, holdback_words: int = 2):
        self.holdback_words = max(1, int(holdback_words))
        self._previous: list[str] = []
        self._stable: list[str] = []

    def update(self, text: str) -> StableTranscript:
        current = (text or "").strip().split()
        stable_prefix = _common_prefix(self._stable, current)
        if len(stable_prefix) < len(self._stable):
            self._stable = stable_prefix

        repeated = _common_prefix(self._previous, current)
        candidate_count = max(0, len(repeated) - self.holdback_words)
        if candidate_count > len(self._stable):
            self._stable = current[:candidate_count]

        self._previous = current
        unstable = current[len(self._stable) :]
        stable_text = " ".join(self._stable)
        unstable_text = " ".join(unstable)
        return StableTranscript(stable_text, unstable_text, " ".join(current))


def _common_prefix(left: list[str], right: list[str]) -> list[str]:
    result: list[str] = []
    for left_token, right_token in zip(left, right):
        if left_token.casefold() != right_token.casefold():
            break
        result.append(right_token)
    return result
