"""Count known grammar errors in a piece of text.

This is a port of Android's ``GrammarQualityGate`` (``OnDeviceGrammarEngine.kt``),
and it exists to answer one comparative question: is the model's answer better
than the repair the rules layer already produced?

Windows previously demanded that a candidate match none of about a hundred
patterns before it could be shown. Measured against the twenty-essay corpus,
that rejected 159 of 200 sections and handed the reader unrepaired writing. A
reader does not need a flawless correction, only a better one, so the engine now
compares this score for the model against the same score for the rules output
and keeps whichever is lower.

The rules below are deliberately general - agreement, determiners, comparatives,
spacing - so the score stays meaningful on writing the test corpus has never
seen. ``polish_quality._RESIDUAL_CHECKS`` remains the richer diagnostic list and
still names what is left in a correction; it is not a scorer, because it records
only whether a pattern appeared, never how often.
"""

from __future__ import annotations

import re

# Python's re has no \p{Ll}. Android uses it so that proper-noun reduplication
# ("Bora Bora", "Walla Walla") is never counted as a repeated word. Plain [a-z]
# preserves that intent for English, which is the only language the engine
# accepts - analyze() gates on english_confidence before it ever scores.
_LOWER = "a-z"

_KNOWN_ERRORS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:I|you|we|they) is\b", re.IGNORECASE),
    re.compile(r"\b(?:he|she|it|this|that) are\b", re.IGNORECASE),
    re.compile(r"\b(?:these|those) is\b", re.IGNORECASE),
    re.compile(r"\bthose included is\b", re.IGNORECASE),
    re.compile(
        r"\b(?:a|an)\s+(?:errors|issues|reports|entries|items|files|documents|"
        r"sentences|paragraphs|sections|tasks|changes|actions|steps|requirements|"
        r"columns|managers|analysts|members|volunteers|shovels|rakes|tools)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:the|a|an)\s+(?:today|tomorrow|yesterday|tonight)\b",
        re.IGNORECASE,
    ),
    # Lowercase-only, matching Android, so reduplicated proper nouns survive.
    re.compile(rf"\b([{_LOWER}][{_LOWER}'’-]*)\s+\1\b"),
    re.compile(r"(?:^|[.!?]\s+)(?:him|her|them) and I\b", re.IGNORECASE),
    re.compile(
        r"\bbetween\s+(?:you|him|her|them|us)\s+and\s+I\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:more\s+(?:better|worse)|most\s+(?:best|worst))\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bthan previously accustomed\b", re.IGNORECASE),
    re.compile(r"^\s*It is why\b", re.IGNORECASE | re.MULTILINE),
    re.compile(
        r"\b(?:tasks|things|items|steps|actions|duties|responsibilities|"
        r"requirements|documents|files|records|changes|issues|errors)\s+that\s+"
        r"(?:needs|requires|includes|contains|provides|helps|makes|takes|uses|works)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bif\b[^.!?]{0,140}\bhad(?:\s+been)?\b[^.!?]{0,140},"
        r"[^.!?]{0,140}\b(?:may|might|could|should|would)\s+(?!have\b)[a-z]+\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d+)\s+"
        r"(?:error|issue|report|entry|item|file|document|sentence|paragraph|section|task)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bdone\s+(?:make|take|write|send|review|check|fix|correct)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:errors?|issues?|reports?|entries|items?|files?|documents?|"
        r"sentences?|paragraphs?|sections?|tasks?)\s+"
        r"(?:I|you|we|they|he|she|it|this|that|there)\s+"
        r"(?:am|are|is|was|were|can|could|will|would|should|must|have|has|do|does)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\band,\s+(?:I|we|you|he|she|it|they|this|that|these|those)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bstuff for peoples\b", re.IGNORECASE),
    re.compile(r"\s+[,.;:!?]"),
    re.compile(r"([.!?]){2,}"),
)

# A sentence that opens lowercase. Not case-insensitive - the lowercase is the error.
_SENTENCE_CASE = re.compile(r"(?:^|[.!?]\s+)[a-z]\b", re.MULTILINE)

# "a dog's bowl" reads as an article followed by a plural to several of the
# rules above. Android subtracts these so a correct possessive never scores.
_POSSESSIVE_FALSE_POSITIVE = re.compile(
    r"\b(?:a|an) (?:[^\W\d_]|-)+['’]s\b",
    re.IGNORECASE,
)

_TERMINAL_PUNCTUATION = ".!?:"


def _count(pattern: re.Pattern[str], value: str) -> int:
    """Number of non-overlapping matches, matching Kotlin's findAll().count()."""
    return sum(1 for _ in pattern.finditer(value))


def error_score(value: str) -> int:
    """Known grammar errors in ``value``. Lower is better; zero is clean.

    Only ever compare two scores. The absolute number is not calibrated to
    anything - it is the difference between a candidate and the deterministic
    repair that decides whether the model's answer is worth showing.
    """
    raw = sum(_count(pattern, value) for pattern in _KNOWN_ERRORS)
    trimmed = value.strip()
    sentence_case = _count(_SENTENCE_CASE, trimmed)
    terminal = 0
    if (
        trimmed
        and trimmed[-1] not in _TERMINAL_PUNCTUATION
        # A heading needs no full stop, so an all-heading block is not missing one.
        and not all(
            line.lstrip().startswith("#") for line in trimmed.splitlines()
        )
    ):
        terminal = 1
    possessive = _count(_POSSESSIVE_FALSE_POSITIVE, value)
    return max(0, raw + sentence_case + terminal - possessive)
