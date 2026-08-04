from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .readability import WORD_PATTERN, analyze_readability


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|[\r\n]+")
_PARAGRAPH_SPLIT = re.compile(r"(?:\r?\n\s*){2,}")
_READING_WORDS_PER_MINUTE = 225
_MAX_FLAG_TERMS = 5
_MAX_LONG_SENTENCE_PREVIEWS = 3

_FILLER_TERMS = (
    "actually",
    "basically",
    "I mean",
    "just",
    "kind of",
    "literally",
    "really",
    "simply",
    "sort of",
    "uh",
    "um",
    "very",
    "you know",
)

_WEAK_WORDING_TERMS = (
    "at this point in time",
    "due to the fact that",
    "I believe",
    "I think",
    "in order to",
    "it is important to",
    "maybe",
    "perhaps",
    "seems",
    "somewhat",
    "stuff",
    "there are",
    "there is",
    "things",
)


@dataclass(frozen=True, slots=True)
class WordingFlag:
    term: str
    count: int


@dataclass(frozen=True, slots=True)
class DocumentAnalytics:
    readability_score: int
    readability_label: str
    grade_level: float
    word_count: int
    sentence_count: int
    paragraph_count: int
    reading_time_minutes: float
    long_sentence_count: int
    long_sentence_previews: tuple[str, ...]
    filler_flags: tuple[WordingFlag, ...]
    weak_wording_flags: tuple[WordingFlag, ...]
    guidance: tuple[str, ...]

    @property
    def filler_count(self) -> int:
        return sum(flag.count for flag in self.filler_flags)

    @property
    def weak_wording_count(self) -> int:
        return sum(flag.count for flag in self.weak_wording_flags)

    @property
    def reading_time_label(self) -> str:
        if self.reading_time_minutes < 1:
            return "Under 1 min"
        return f"{math.ceil(self.reading_time_minutes)} min"


def analyze_document(text: str) -> DocumentAnalytics:
    """Return deterministic, local-only metrics for one document draft."""
    source = (text or "").strip()
    if not source:
        raise ValueError("Write or load document text first.")

    readability = analyze_readability(source)
    paragraphs = [part.strip() for part in _PARAGRAPH_SPLIT.split(source) if part.strip()]
    sentences = [part.strip() for part in _SENTENCE_SPLIT.split(source) if part.strip()]
    long_sentences = [
        sentence
        for sentence in sentences
        if len(WORD_PATTERN.findall(sentence)) > 25
    ]
    filler_flags = _find_terms(source, _FILLER_TERMS)
    weak_flags = _find_terms(source, _WEAK_WORDING_TERMS)
    reading_minutes = readability.word_count / _READING_WORDS_PER_MINUTE

    guidance: list[str] = []
    if long_sentences:
        guidance.append(
            f"Break up {len(long_sentences)} sentence(s) longer than 25 words."
        )
    filler_count = sum(flag.count for flag in filler_flags)
    if filler_count:
        guidance.append(
            f"Cut or replace {filler_count} filler use(s): {_term_summary(filler_flags)}."
        )
    weak_count = sum(flag.count for flag in weak_flags)
    if weak_count:
        guidance.append(
            f"Strengthen {weak_count} weak phrase(s): {_term_summary(weak_flags)}."
        )
    if readability.score < 60 and not long_sentences:
        guidance.append("Use shorter sentences and simpler words to improve readability.")
    if not guidance:
        guidance.append("No common document-level problems found.")

    return DocumentAnalytics(
        readability_score=readability.score,
        readability_label=readability.label,
        grade_level=readability.grade_level,
        word_count=readability.word_count,
        sentence_count=readability.sentence_count,
        paragraph_count=max(1, len(paragraphs)),
        reading_time_minutes=round(max(reading_minutes, 0.1), 1),
        long_sentence_count=len(long_sentences),
        long_sentence_previews=tuple(
            _preview_sentence(sentence)
            for sentence in long_sentences[:_MAX_LONG_SENTENCE_PREVIEWS]
        ),
        filler_flags=filler_flags,
        weak_wording_flags=weak_flags,
        guidance=tuple(guidance),
    )


def _find_terms(source: str, terms: tuple[str, ...]) -> tuple[WordingFlag, ...]:
    flags: list[WordingFlag] = []
    for term in terms:
        words = re.split(r"\s+", term.strip())
        body = r"\s+".join(re.escape(word) for word in words)
        pattern = re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)
        count = len(pattern.findall(source))
        if count:
            flags.append(WordingFlag(term=term, count=count))
    return tuple(flags)


def _term_summary(flags: tuple[WordingFlag, ...]) -> str:
    shown = flags[:_MAX_FLAG_TERMS]
    value = ", ".join(
        f'"{flag.term}"' + (f" x{flag.count}" if flag.count > 1 else "")
        for flag in shown
    )
    hidden = len(flags) - len(shown)
    return f"{value}, plus {hidden} more" if hidden else value


def _preview_sentence(sentence: str) -> str:
    compact = re.sub(r"\s+", " ", sentence).strip()
    return compact if len(compact) <= 120 else compact[:117].rstrip() + "..."


__all__ = ["DocumentAnalytics", "WordingFlag", "analyze_document"]
