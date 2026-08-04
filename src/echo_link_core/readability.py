from __future__ import annotations

import re
from dataclasses import dataclass


WORD_PATTERN = re.compile(r"[A-Za-z]+(?:['’-][A-Za-z]+)*|\d+(?:[.,]\d+)*")
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|[\r\n]+")
FILLERS = {"actually", "basically", "just", "literally", "really", "simply", "very"}
PASSIVE_PATTERN = re.compile(
    r"\b(?:am|is|are|was|were|be|been|being)\s+[a-z]+(?:ed|en)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ReadabilityReport:
    score: int
    label: str
    grade_level: float
    word_count: int
    sentence_count: int
    average_sentence_words: float
    long_sentence_count: int
    complex_word_count: int
    filler_count: int
    passive_voice_count: int
    suggestions: tuple[str, ...]

    def as_text(self) -> str:
        lines = [
            f"Readability score: {self.score}/100 ({self.label})",
            f"Estimated grade level: {self.grade_level:.1f}",
            f"Words: {self.word_count}",
            f"Sentences: {self.sentence_count}",
            f"Average sentence length: {self.average_sentence_words:.1f} words",
            f"Long sentences: {self.long_sentence_count}",
            f"Complex words: {self.complex_word_count}",
            f"Filler words: {self.filler_count}",
            f"Possible passive voice: {self.passive_voice_count}",
            "",
            "Suggestions:",
        ]
        lines.extend(f"- {item}" for item in self.suggestions)
        return "\n".join(lines)


def analyze_readability(text: str) -> ReadabilityReport:
    source = (text or "").strip()
    if not source:
        raise ValueError("Select text first.")
    words = WORD_PATTERN.findall(source)
    if not words:
        raise ValueError("No readable words were found.")
    sentences = [item.strip() for item in SENTENCE_SPLIT.split(source) if item.strip()]
    sentence_count = max(1, len(sentences))
    syllables = sum(_syllable_count(word) for word in words)
    average_sentence_words = len(words) / sentence_count
    syllables_per_word = syllables / len(words)
    ease = 206.835 - 1.015 * average_sentence_words - 84.6 * syllables_per_word
    score = int(round(max(0.0, min(100.0, ease))))
    grade = max(0.0, 0.39 * average_sentence_words + 11.8 * syllables_per_word - 15.59)
    long_sentences = sum(len(WORD_PATTERN.findall(sentence)) > 25 for sentence in sentences)
    complex_words = sum(_syllable_count(word) >= 3 for word in words if word.isalpha())
    filler_count = sum(word.casefold() in FILLERS for word in words)
    passive_count = len(PASSIVE_PATTERN.findall(source))

    suggestions: list[str] = []
    if long_sentences:
        suggestions.append(f"Split {long_sentences} sentence(s) longer than 25 words.")
    if complex_words / len(words) > 0.15:
        suggestions.append("Replace some complex words with simpler terms.")
    if filler_count:
        suggestions.append(f"Remove or review {filler_count} filler word(s).")
    if passive_count:
        suggestions.append(f"Review {passive_count} possible passive-voice phrase(s).")
    if not suggestions:
        suggestions.append("No common readability problems found.")

    return ReadabilityReport(
        score=score,
        label=_score_label(score),
        grade_level=round(grade, 1),
        word_count=len(words),
        sentence_count=sentence_count,
        average_sentence_words=round(average_sentence_words, 1),
        long_sentence_count=long_sentences,
        complex_word_count=complex_words,
        filler_count=filler_count,
        passive_voice_count=passive_count,
        suggestions=tuple(suggestions),
    )


def _score_label(score: int) -> str:
    if score >= 80:
        return "Easy"
    if score >= 60:
        return "Clear"
    if score >= 40:
        return "Difficult"
    return "Very difficult"


def _syllable_count(word: str) -> int:
    value = re.sub(r"[^a-z]", "", word.casefold())
    if not value:
        return 1
    groups = re.findall(r"[aeiouy]+", value)
    count = len(groups)
    if value.endswith("e") and not value.endswith(("le", "ye")) and count > 1:
        count -= 1
    return max(1, count)
