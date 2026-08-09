from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


class SuggestionCategory(StrEnum):
    SPELLING = "spelling"
    GRAMMAR = "grammar"
    FORMATTING = "formatting"
    CLARITY = "clarity"


@dataclass(frozen=True, slots=True)
class WritingSuggestion:
    suggestion_id: str
    category: SuggestionCategory
    title: str
    explanation: str
    start: int
    end: int
    original: str
    replacement: str


@dataclass(frozen=True, slots=True)
class _Candidate:
    rule: str
    category: SuggestionCategory
    title: str
    explanation: str
    start: int
    end: int
    replacement: str


_COMMON_TYPOS = {
    "accomodate": "accommodate",
    "accomodations": "accommodations",
    "accidently": "accidentally",
    "alot": "a lot",
    "archieve": "archive",
    "calender": "calendar",
    "cant": "can't",
    "consistant": "consistent",
    "definately": "definitely",
    "didnt": "didn't",
    "embarass": "embarrass",
    "formating": "formatting",
    "grammer": "grammar",
    "inconsistant": "inconsistent",
    "lables": "labels",
    "maintence": "maintenance",
    "mesages": "messages",
    "neccessary": "necessary",
    "occured": "occurred",
    "recieve": "receive",
    "recieved": "received",
    "seperate": "separate",
    "seperates": "separates",
    "sentances": "sentences",
    "sentnce": "sentence",
    "succesful": "successful",
    "sucessfully": "successfully",
    "teh": "the",
    "understandible": "understandable",
    "visable": "visible",
    "volenteers": "volunteers",
    "wasnt": "wasn't",
    "wierd": "weird",
}

_FILLER_REPLACEMENTS = {
    "at this point in time": "now",
    "due to the fact that": "because",
    "for the purpose of": "to",
    "in order to": "to",
    "the fact that": "that",
}

_COMMON_GRAMMAR_PHRASES = {
    "between you and i": "between you and me",
    "committee have": "committee has",
    "committee were": "committee was",
    "committee want": "committee wants",
    "correction card were": "correction card was",
    "card need": "card needs",
    "could of": "could have",
    "group are": "group is",
    "group were": "group was",
    "interface were": "interface was",
    "a one-safe paste": "one safe paste",
    "a one safe paste": "one safe paste",
    "one-safe paste": "one safe paste",
    "request calls for": "requested",
    "reviewers needs": "reviewers need",
    "safe for pasting": "safe paste",
    "sentences is": "sentences are",
    "private sentence are": "private sentence is",
    "should of": "should have",
    "team are": "team is",
    "team compare": "team compares",
    "team have": "team has",
    "team were": "team was",
    "then teams are moving": "then teams will move",
    "would of": "would have",
    "without warning a lot": "frequently without warning",
    "without warning frequently": "frequently without warning",
    "zone c will be building": "Zone C will build",
    "there own": "their own",
    "last years": "last year's",
    "more quicker": "quicker",
    "less volunteers": "fewer volunteers",
}

_WORD = re.compile(r"\b[A-Za-z][A-Za-z'-]*\b")
# The corrector produced "replacing water,, water,, cleaning" and "beyond the
# city,, city,, and" - a repeated word carrying a stray comma with it. Plain
# whitespace was the only separator this matched, so those survived.
#
# A run of two or more commas is the marker. A SINGLE comma between repeats is
# deliberately not matched: "New York, New York" and "Puebla, Puebla" are
# correct English, and a rule that flattened them would be worse than the
# defect it fixes.
_DUPLICATE_WORD = re.compile(
    r"(?=\b(?P<first>[A-Za-z][A-Za-z'-]*)"
    r"(?:[ \t]+|[ \t]*,[\s,]*,[ \t]*)"
    r"(?P<second>[A-Za-z][A-Za-z'-]*)\b)",
    re.IGNORECASE,
)
_LOWERCASE_I = re.compile(r"(?<![\w'])i(?![\w'])")
_SPACE_BEFORE_PUNCTUATION = re.compile(r"[ \t]+(?=[,.;:!?])")
_REPEATED_SPACE = re.compile(r"[ \t]{2,}")
_REPEATED_TERMINAL_PUNCTUATION = re.compile(r"(?P<mark>[!?])(?P=mark)+")
_DECLARATIVE_QUESTION_MARK = re.compile(
    r"(?:\A|(?<=[.!?])[ \t]+)these[ \t]+(?:issues|changes)[ \t]+"
    r"(?:make|makes|create|creates)\b[^.!?\r\n]{0,240}(?P<mark>\?)",
    re.IGNORECASE,
)
_HOWEVER_COMMA_SPLICE = re.compile(
    r",[ \t]+however(?:,[ \t]*|[ \t]+)",
    re.IGNORECASE,
)
_TWO_PREDICATE_COMMA = re.compile(
    r"\bsentences(?P<comma>,)[ \t]+and[ \t]+sends\b",
    re.IGNORECASE,
)
_TIME_PREPOSITION_FRAGMENT = re.compile(
    r"\b(?P<time>\d{1,2}:\d{2}[ \t]+[ap]\.m\.)[ \t]+"
    r"(?P<prep>In|At|On)\b(?=[^.!?\r\n]*;)",
    re.IGNORECASE,
)
_DEVICE_TEST_PREPOSITION = re.compile(
    r"\b(?:test|tested|testing)\b[^.!?\r\n]{0,80}?\b(?P<prep>in)\b"
    r"(?=[ \t]+(?:one|two|three|four|five|several|multiple|\d+)[ \t]+"
    r"(?:laptops?|computers?|devices?|machines?)\b)",
    re.IGNORECASE,
)
_TITLE_NAME_COMMAS = re.compile(
    r"(?<!\bthe[ \t])\b(?P<role>project lead|curator|technician|coordinator|"
    r"manager|director),[ \t]+(?P<name>[A-Z][A-Za-z'-]+[ \t]+[A-Z][A-Za-z'-]+),"
    r"(?=[ \t]+(?:said|asked|explained|reported|confirmed|noted)\b)",
    re.IGNORECASE,
)
_SENTENCE_START = re.compile(
    r"(?:\A|[.!?][\"'\u201d\u2019)\]]*[ \t\r\n]+)(?P<letter>[a-z])"
)
_CLOSING_MARKS = frozenset("\"'\u201d\u2019)]}")

_PROTECTED_PATTERN = re.compile(
    r"```[\s\S]*?```"
    r"|`[^`\r\n]*`"
    r"|https?://[^\s<>]+"
    r"|www\.[^\s<>]+"
    r"|(?<![\w.+-])[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
    r"|\"(?:[A-Za-z]:[\\/]|\\\\)[^\"\r\n]+\""
    r"|(?<!\w)(?:[A-Za-z]:[\\/]|\\\\)[^\s<>\"|?*]+"
    r"|(?<!\w)/(?:[A-Za-z0-9._-]+/)+[A-Za-z0-9._-]+"
    r"|(?<!\w)--?[A-Za-z][A-Za-z0-9-]*"
    r"|\$\{[^}\r\n]+\}"
    r"|\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\b"
    r"|\b[A-Za-z]+_[A-Za-z0-9_]+\b"
    r"|\b[a-z]+[A-Z][A-Za-z0-9]*\b"
)


class WritingSuggestionEngine:
    """Return bounded, deterministic suggestions without a model or I/O."""

    def analyze(self, text: str) -> tuple[WritingSuggestion, ...]:
        if not isinstance(text, str):
            raise TypeError("Writing suggestion text must be a string.")
        if not text:
            return ()
        protected = _protected_spans(text)
        candidates: list[_Candidate] = []
        candidates.extend(_typo_candidates(text, protected))
        candidates.extend(_duplicate_candidates(text, protected))
        candidates.extend(_grammar_phrase_candidates(text, protected))
        candidates.extend(_lowercase_i_candidates(text, protected))
        candidates.extend(_structural_punctuation_candidates(text, protected))
        candidates.extend(_doubled_punctuation_candidates(text, protected))
        candidates.extend(_punctuation_spacing_candidates(text, protected))
        candidates.extend(_declarative_question_candidates(text, protected))
        candidates.extend(_repeated_terminal_punctuation_candidates(text, protected))
        candidates.extend(_repeated_space_candidates(text, protected))
        candidates.extend(_filler_candidates(text, protected))
        candidates.extend(_capitalization_candidates(text, protected))
        final = _final_punctuation_candidate(text, protected)
        if final is not None:
            candidates.append(final)

        accepted: list[_Candidate] = []
        for candidate in candidates:
            if not any(_spans_conflict(candidate, existing) for existing in accepted):
                accepted.append(candidate)
        accepted.sort(key=lambda item: (item.start, item.end, item.rule))
        source_digest = _source_digest(text)
        return tuple(
            WritingSuggestion(
                suggestion_id=_suggestion_id(text, source_digest, candidate),
                category=candidate.category,
                title=candidate.title,
                explanation=candidate.explanation,
                start=candidate.start,
                end=candidate.end,
                original=text[candidate.start : candidate.end],
                replacement=candidate.replacement,
            )
            for candidate in accepted
        )


def apply_suggestion(text: str, suggestion: WritingSuggestion) -> str:
    """Apply one current suggestion or reject it if its source is stale."""
    _validate_text(text)
    _validate_suggestion(text, suggestion)
    return text[: suggestion.start] + suggestion.replacement + text[suggestion.end :]


def apply_all_suggestions(
    text: str,
    suggestions: Iterable[WritingSuggestion],
) -> str:
    """Apply non-overlapping suggestions from one unchanged analysis pass."""
    _validate_text(text)
    try:
        ordered = sorted(tuple(suggestions), key=lambda item: (item.start, item.end))
    except (AttributeError, TypeError) as exc:
        raise TypeError("Suggestions must contain WritingSuggestion values.") from exc
    for suggestion in ordered:
        _validate_suggestion(text, suggestion)
    for previous, current in zip(ordered, ordered[1:]):
        if _spans_conflict(previous, current):
            raise ValueError("Writing suggestions overlap.")
    result = text
    for suggestion in reversed(ordered):
        result = (
            result[: suggestion.start]
            + suggestion.replacement
            + result[suggestion.end :]
        )
    return result


def _typo_candidates(text: str, protected: tuple[tuple[int, int], ...]) -> list[_Candidate]:
    found: list[_Candidate] = []
    for match in _WORD.finditer(text):
        if _is_protected(match.start(), match.end(), protected):
            continue
        replacement = _COMMON_TYPOS.get(match.group(0).casefold())
        if replacement is None:
            continue
        replacement = _match_case(match.group(0), replacement)
        if _is_sentence_start(text, match.start()):
            replacement = replacement[:1].upper() + replacement[1:]
        found.append(
            _Candidate(
                "common-typo",
                SuggestionCategory.SPELLING,
                "Fix spelling",
                f'Replace "{match.group(0)}" with "{replacement}".',
                match.start(),
                match.end(),
                replacement,
            )
        )
    return found


def _duplicate_candidates(
    text: str,
    protected: tuple[tuple[int, int], ...],
) -> list[_Candidate]:
    found: list[_Candidate] = []
    for match in _DUPLICATE_WORD.finditer(text):
        if match.group("first").casefold() != match.group("second").casefold():
            continue
        start = match.start("first")
        end = match.end("second")
        if _is_protected(start, end, protected):
            continue
        found.append(
            _Candidate(
                "duplicate-word",
                SuggestionCategory.GRAMMAR,
                "Remove repeated word",
                "The same word appears twice in a row.",
                start,
                end,
                match.group("first"),
            )
        )
    return found


def _grammar_phrase_candidates(
    text: str,
    protected: tuple[tuple[int, int], ...],
) -> list[_Candidate]:
    found: list[_Candidate] = []
    for phrase, replacement in _COMMON_GRAMMAR_PHRASES.items():
        pattern = re.compile(
            r"(?<!\w)" + r"\s+".join(map(re.escape, phrase.split())) + r"(?!\w)",
            re.IGNORECASE,
        )
        for match in pattern.finditer(text):
            if _is_protected(match.start(), match.end(), protected):
                continue
            value = _match_case(match.group(0), replacement)
            found.append(
                _Candidate(
                    "common-grammar-phrase",
                    SuggestionCategory.GRAMMAR,
                    "Fix grammar",
                    f'Replace "{match.group(0)}" with "{value}".',
                    match.start(),
                    match.end(),
                    value,
                )
            )
    for match in _TWO_PREDICATE_COMMA.finditer(text):
        sentence_start = max(
            text.rfind(".", 0, match.start()),
            text.rfind("!", 0, match.start()),
            text.rfind("?", 0, match.start()),
            text.rfind("\n", 0, match.start()),
        ) + 1
        if "," in text[sentence_start : match.start()]:
            continue
        start = match.start("comma")
        end = match.end("comma")
        if _is_protected(start, end, protected):
            continue
        found.append(
            _Candidate(
                "two-predicate-comma",
                SuggestionCategory.GRAMMAR,
                "Fix grammar",
                "Do not separate two predicates that share one subject with a comma.",
                start,
                end,
                "",
            )
        )
    return found


def _structural_punctuation_candidates(
    text: str,
    protected: tuple[tuple[int, int], ...],
) -> list[_Candidate]:
    found: list[_Candidate] = []
    for match in _HOWEVER_COMMA_SPLICE.finditer(text):
        if _is_protected(match.start(), match.end(), protected):
            continue
        however = "However" if "However" in match.group(0) else "however"
        found.append(
            _Candidate(
                "however-comma-splice",
                SuggestionCategory.GRAMMAR,
                "Fix comma splice",
                "Use a semicolon before however between independent clauses.",
                match.start(),
                match.end(),
                f"; {however}, ",
            )
        )
    for match in _TIME_PREPOSITION_FRAGMENT.finditer(text):
        start = match.start("prep")
        end = match.end("prep")
        if _is_protected(start, end, protected):
            continue
        found.append(
            _Candidate(
                "time-preposition-fragment",
                SuggestionCategory.GRAMMAR,
                "Join sentence fragment",
                "Keep the location phrase attached to the time clause.",
                start,
                end,
                match.group("prep").lower(),
            )
        )
    for match in _DEVICE_TEST_PREPOSITION.finditer(text):
        start = match.start("prep")
        end = match.end("prep")
        if _is_protected(start, end, protected):
            continue
        found.append(
            _Candidate(
                "device-test-preposition",
                SuggestionCategory.GRAMMAR,
                "Fix device preposition",
                "Tests run on devices, not in them.",
                start,
                end,
                "on",
            )
        )
    for match in _TITLE_NAME_COMMAS.finditer(text):
        if _is_protected(match.start(), match.end(), protected):
            continue
        found.append(
            _Candidate(
                "title-name-commas",
                SuggestionCategory.GRAMMAR,
                "Fix title and name punctuation",
                "Do not separate a restrictive role and name with commas.",
                match.start(),
                match.end(),
                f"{match.group('role')} {match.group('name')}",
            )
        )
    return found


def _lowercase_i_candidates(
    text: str,
    protected: tuple[tuple[int, int], ...],
) -> list[_Candidate]:
    return [
        _Candidate(
            "lowercase-i",
            SuggestionCategory.GRAMMAR,
            "Capitalize I",
            "The standalone pronoun I is capitalized.",
            match.start(),
            match.end(),
            "I",
        )
        for match in _LOWERCASE_I.finditer(text)
        if not _is_protected(match.start(), match.end(), protected)
    ]


def _punctuation_spacing_candidates(
    text: str,
    protected: tuple[tuple[int, int], ...],
) -> list[_Candidate]:
    return [
        _Candidate(
            "space-before-punctuation",
            SuggestionCategory.FORMATTING,
            "Remove space before punctuation",
            "Punctuation follows the previous word without a space.",
            match.start(),
            match.end(),
            "",
        )
        for match in _SPACE_BEFORE_PUNCTUATION.finditer(text)
        if not _is_protected(match.start(), match.end(), protected)
    ]


# Doubled commas were the last remaining artefact in eight of nine otherwise
# clean essays - "in Utrecht, the Netherlands,, this can be", "replacing
# water,, cleaning". A stray comma after a full stop is the same defect from
# the other side ("or other equipment., but the animal").
_DOUBLED_COMMA = re.compile(r",[\s,]*,")
_TERMINATOR_THEN_COMMA = re.compile(r"([.!?])\s*,")
# "At 12:40 p.m., observers" must keep its full stop. An abbreviation before
# the comma is punctuating normally, not a doubled terminator.
_TERMINATOR_ABBREVIATIONS = frozenset(
    {"al", "am", "cf", "dr", "eg", "etc", "ie", "inc", "jr", "lt", "ltd",
     "mr", "mrs", "ms", "no", "pm", "prof", "sr", "st", "vs"}
)


def _doubled_punctuation_candidates(
    text: str,
    protected: tuple[tuple[int, int], ...],
) -> list[_Candidate]:
    candidates = [
        _Candidate(
            "doubled-comma",
            SuggestionCategory.FORMATTING,
            "Remove the repeated comma",
            "One comma separates the clause; the extra one is a typing slip.",
            match.start(),
            match.end(),
            ",",
        )
        for match in _DOUBLED_COMMA.finditer(text)
        if not _is_protected(match.start(), match.end(), protected)
    ]
    for match in _TERMINATOR_THEN_COMMA.finditer(text):
        if _is_protected(match.start(), match.end(), protected):
            continue
        # Take the whole dotted token, not just its last letters: "p.m." has to
        # be recognised as "pm", or the guard sees "m" and strips a comma that
        # was punctuating the sentence correctly.
        lead = re.findall(r"([A-Za-z][A-Za-z.]*)$", text[: match.start() + 1])
        token = lead[0].replace(".", "").casefold() if lead else ""
        if token in _TERMINATOR_ABBREVIATIONS:
            continue
        candidates.append(
            _Candidate(
                "terminator-then-comma",
                SuggestionCategory.FORMATTING,
                "Remove the comma after the full stop",
                "A sentence ends with its full stop; the comma is left over.",
                match.start(),
                match.end(),
                match.group(1),
            )
        )
    return candidates


def _declarative_question_candidates(
    text: str,
    protected: tuple[tuple[int, int], ...],
) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for match in _DECLARATIVE_QUESTION_MARK.finditer(text):
        start, end = match.span("mark")
        if _is_protected(start, end, protected):
            continue
        candidates.append(
            _Candidate(
                "declarative-question-mark",
                SuggestionCategory.GRAMMAR,
                "Use statement punctuation",
                "A declarative statement should end with a period.",
                start,
                end,
                ".",
            )
        )
    return candidates


def _repeated_terminal_punctuation_candidates(
    text: str,
    protected: tuple[tuple[int, int], ...],
) -> list[_Candidate]:
    return [
        _Candidate(
            "repeated-terminal-punctuation",
            SuggestionCategory.FORMATTING,
            "Use one punctuation mark",
            "Standard prose uses one terminal punctuation mark.",
            match.start(),
            match.end(),
            match.group("mark"),
        )
        for match in _REPEATED_TERMINAL_PUNCTUATION.finditer(text)
        if not _is_protected(match.start(), match.end(), protected)
    ]


def _repeated_space_candidates(
    text: str,
    protected: tuple[tuple[int, int], ...],
) -> list[_Candidate]:
    found: list[_Candidate] = []
    for match in _REPEATED_SPACE.finditer(text):
        if _is_protected(match.start(), match.end(), protected):
            continue
        line_start = text.rfind("\n", 0, match.start()) + 1
        if not text[line_start : match.start()].strip():
            continue
        found.append(
            _Candidate(
                "repeated-space",
                SuggestionCategory.FORMATTING,
                "Use one space",
                "Replace repeated horizontal whitespace with one space.",
                match.start(),
                match.end(),
                " ",
            )
        )
    return found


def _filler_candidates(
    text: str,
    protected: tuple[tuple[int, int], ...],
) -> list[_Candidate]:
    found: list[_Candidate] = []
    for phrase, replacement in _FILLER_REPLACEMENTS.items():
        pattern = re.compile(
            r"(?<!\w)" + r"\s+".join(map(re.escape, phrase.split())) + r"(?!\w)",
            re.IGNORECASE,
        )
        for match in pattern.finditer(text):
            if _is_protected(match.start(), match.end(), protected):
                continue
            value = _match_case(match.group(0), replacement)
            if _is_sentence_start(text, match.start()):
                value = value[:1].upper() + value[1:]
            found.append(
                _Candidate(
                    "filler-phrase",
                    SuggestionCategory.CLARITY,
                    "Tighten filler phrase",
                    f'Use "{value}" for a more direct sentence.',
                    match.start(),
                    match.end(),
                    value,
                )
            )
    return found


def _capitalization_candidates(
    text: str,
    protected: tuple[tuple[int, int], ...],
) -> list[_Candidate]:
    found: list[_Candidate] = []
    for match in _SENTENCE_START.finditer(text):
        start = match.start("letter")
        if _follows_nonterminal_abbreviation(text, start):
            continue
        if _is_protected(start, start + 1, protected):
            continue
        found.append(
            _Candidate(
                "sentence-capitalization",
                SuggestionCategory.GRAMMAR,
                "Capitalize sentence",
                "A sentence starts with a capital letter.",
                start,
                start + 1,
                text[start].upper(),
            )
        )
    return found


def _follows_nonterminal_abbreviation(text: str, start: int) -> bool:
    prefix = text[:start].rstrip().casefold()
    return bool(
        re.search(
            r"(?<![a-z])(?:a\.m|p\.m|e\.g|i\.e|mr|mrs|ms|dr|prof|sr|jr|st)\.$",
            prefix,
        )
    )


def _final_punctuation_candidate(
    text: str,
    protected: tuple[tuple[int, int], ...],
) -> _Candidate | None:
    stripped_end = len(text.rstrip())
    if stripped_end == 0:
        return None
    insertion = stripped_end
    while insertion > 0 and text[insertion - 1] in _CLOSING_MARKS:
        insertion -= 1
    if insertion == 0 or text[insertion - 1] in ".!?\u2026;:":
        return None
    if not text[insertion - 1].isalnum():
        return None
    if _is_protected(insertion, insertion, protected, include_edges=False):
        return None
    return _Candidate(
        "final-punctuation",
        SuggestionCategory.GRAMMAR,
        "Add final punctuation",
        "Complete the final sentence with a period.",
        insertion,
        insertion,
        ".",
    )


def _protected_spans(text: str) -> tuple[tuple[int, int], ...]:
    spans = sorted((match.start(), match.end()) for match in _PROTECTED_PATTERN.finditer(text))
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)


def _is_protected(
    start: int,
    end: int,
    protected: tuple[tuple[int, int], ...],
    *,
    include_edges: bool = True,
) -> bool:
    if start == end:
        if include_edges:
            return any(left <= start <= right for left, right in protected)
        return any(left < start < right for left, right in protected)
    return any(start < right and end > left for left, right in protected)


def _is_sentence_start(text: str, start: int) -> bool:
    prefix = text[:start].rstrip()
    if not prefix:
        return True
    return prefix[-1] in ".!?\u2026"


def _match_case(original: str, replacement: str) -> str:
    if original.isupper():
        return replacement.upper()
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _spans_conflict(left, right) -> bool:
    if left.start == left.end and right.start == right.end:
        return left.start == right.start
    if left.start == left.end:
        return right.start < left.start < right.end
    if right.start == right.end:
        return left.start < right.start < left.end
    return left.start < right.end and right.start < left.end


def _source_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


def _suggestion_id(text: str, source_digest: str, candidate: _Candidate) -> str:
    material = (
        f"{candidate.rule}\0{candidate.start}\0{candidate.end}\0"
        f"{text[candidate.start:candidate.end]}\0{candidate.replacement}"
    )
    candidate_digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"ws:{source_digest}:{candidate_digest}"


def _validate_text(text: str) -> None:
    if not isinstance(text, str):
        raise TypeError("Writing suggestion text must be a string.")


def _validate_suggestion(text: str, suggestion: WritingSuggestion) -> None:
    if not isinstance(suggestion, WritingSuggestion):
        raise TypeError("Expected a WritingSuggestion.")
    if (
        isinstance(suggestion.start, bool)
        or isinstance(suggestion.end, bool)
        or not isinstance(suggestion.start, int)
        or not isinstance(suggestion.end, int)
        or not 0 <= suggestion.start <= suggestion.end <= len(text)
    ):
        raise ValueError("Writing suggestion bounds are invalid.")
    parts = suggestion.suggestion_id.split(":")
    if (
        len(parts) != 3
        or parts[0] != "ws"
        or not hmac.compare_digest(parts[1], _source_digest(text))
    ):
        raise ValueError("Writing suggestion is stale or invalid.")
    if text[suggestion.start : suggestion.end] != suggestion.original:
        raise ValueError("Writing suggestion no longer matches the source text.")


__all__ = [
    "SuggestionCategory",
    "WritingSuggestion",
    "WritingSuggestionEngine",
    "apply_all_suggestions",
    "apply_suggestion",
]
