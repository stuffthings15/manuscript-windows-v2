"""Where a review is cut into sections, and what changed inside one.

Split out of rewrite_palette.py. None of it touches Qt: these are decisions
about text - where a sentence ends, how large a review unit may be, which
tokens a suggestion replaced - and they were the half of that module a reader
could reason about without a running widget.
"""

from __future__ import annotations

import difflib
import hashlib
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace

logger = logging.getLogger(__name__)


FILTERS = ("All", "Spelling", "Grammar", "Formatting", "Clarity")


_REVIEW_MAX_CHARACTERS = 1_200


_REVIEW_MAX_WORDS = 180


@dataclass(frozen=True, slots=True)
class _SentenceSuggestion:
    suggestion_id: str
    category: str
    categories: tuple[str, ...]
    title: str
    explanation: str
    start: int
    end: int
    original: str
    replacement: str
    source_digest: str
    edit_count: int
    has_changes: bool
    sentence_count: int


def _group_sentence_suggestions(
    text: str,
    suggestions: Sequence[object],
) -> tuple[_SentenceSuggestion, ...]:
    # Sections must not overlap, and every edit must sit inside the section it
    # is filed under. Both were violated by filing an edge case under its own
    # sentence bounds and hoping those bounds did not collide with a review
    # span that already existed.
    #
    # Measured on the 500-percent corpus, 2026-08-15: 30 overlapping pairs
    # across 15 of the 20 essays, nearly all of them a whitespace run like
    # "    ." sitting against the sentence that follows it. Overlap is not
    # cosmetic. Accepting one section leaves its overlapping sibling
    # unshiftable, `_shift_remaining_sentence_suggestions` refuses with
    # "Pending sentence suggestions overlap accepted text", and the review
    # cannot advance: essay 16 spent 457 seconds retrying 25 sections 56 times
    # and left the whole tail of the document uncorrected.
    #
    # So the bounds are decided first and merged into disjoint intervals, and
    # only then do edits get filed. An edit can then always be placed, and
    # placed in exactly one section.
    review_spans = _review_spans(text)
    bounds_by_start: list[tuple[int, int, int]] = list(review_spans)
    for suggestion in suggestions:
        start = int(getattr(suggestion, "start"))
        end = int(getattr(suggestion, "end"))
        if any(item[0] <= start and end <= item[1] for item in bounds_by_start):
            continue
        left, right = _sentence_bounds(text, start, end)
        bounds_by_start.append((min(left, start), max(right, end), 1))

    merged: list[list[int]] = []
    for left, right, count in sorted(bounds_by_start):
        if merged and left < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], right)
            merged[-1][2] += count
        else:
            merged.append([left, right, count])

    grouped: dict[tuple[int, int, int], list[object]] = {
        (left, right, count): [] for left, right, count in merged
    }
    for suggestion in suggestions:
        start = int(getattr(suggestion, "start"))
        end = int(getattr(suggestion, "end"))
        bounds = next(
            (item for item in grouped if item[0] <= start and end <= item[1]),
            None,
        )
        if bounds is None:
            # Nothing to file it under without reintroducing an overlap. Say
            # so rather than dropping it in silence.
            logger.warning(
                "Review span found no section for an edit: edit=[%d,%d)", start, end
            )
            continue
        grouped[bounds].append(suggestion)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]
    result: list[_SentenceSuggestion] = []
    for (start, end, sentence_count), edits in sorted(grouped.items()):
        original = text[start:end]
        replacement = original
        applied = []
        for edit in sorted(edits, key=lambda item: (int(item.start), int(item.end)), reverse=True):
            local_start = int(edit.start) - start
            local_end = int(edit.end) - start
            expected = str(edit.original)
            # Refusing to apply an edit whose source text has moved is a
            # safety property and it stays. Throwing that refusal out of this
            # function was not: one unusable edit discarded every section in
            # the document, and the caller turned that into a badge reading
            # "0 reviews ready" with nothing in the log to say why. The edit
            # is dropped, loudly, and the other sections survive.
            if local_start < 0 or replacement[local_start:local_end] != expected:
                logger.warning(
                    "Review span dropped an edit that no longer matches its "
                    "source: span=[%d,%d) edit=[%d,%d) title=%r",
                    start,
                    end,
                    int(edit.start),
                    int(edit.end),
                    str(edit.title),
                )
                continue
            applied.append(edit)
            replacement = replacement[:local_start] + str(edit.replacement) + replacement[local_end:]
        edits = applied
        categories = tuple(dict.fromkeys(_category_name(item) for item in edits))
        titles = tuple(dict.fromkeys(str(item.title).strip() for item in edits))
        explanations = tuple(
            dict.fromkeys(str(item.explanation).strip() for item in edits if str(item.explanation).strip())
        )
        edit_count = len(edits)
        if edit_count == 0:
            title = "Review text"
            explanation = "No rule-based correction found. Local AI will proofread this text."
        else:
            title = titles[0] if edit_count == 1 else f"Improve sentence ({edit_count} edits)"
            explanation = " ".join(explanations)
        material = f"{start}\0{end}\0{original}\0{replacement}"
        suggestion_digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
        result.append(
            _SentenceSuggestion(
                suggestion_id=f"sentence:{digest}:{suggestion_digest}",
                category=categories[0] if categories else "Review",
                categories=categories or ("Review",),
                title=title,
                explanation=explanation,
                start=start,
                end=end,
                original=original,
                replacement=replacement,
                source_digest=digest,
                edit_count=edit_count,
                has_changes=replacement != original,
                sentence_count=sentence_count,
            )
        )
    return tuple(result)


_REVIEW_LITERAL = re.compile(
    r"\b\d[\d,:.-]*\b|"
    r"\b(?:[A-Z][a-z]+(?:\s+)){1,3}[A-Z][a-z]+\b"
)


def _review_literal_burden(text: str) -> int:
    return len(tuple(_REVIEW_LITERAL.finditer(text)))


_DIFF_TOKEN = re.compile(r"\s+|[^\W_]+(?:['’][^\W_]+)*|[^\s\w]", re.UNICODE)


def _diff_tokens(value: str) -> tuple[tuple[str, int, int], ...]:
    """Tokens with their character spans, covering the whole string."""
    tokens: list[tuple[str, int, int]] = []
    position = 0
    for match in _DIFF_TOKEN.finditer(value):
        if match.start() > position:
            tokens.append((value[position : match.start()], position, match.start()))
        tokens.append((match.group(), match.start(), match.end()))
        position = match.end()
    if position < len(value):
        tokens.append((value[position:], position, len(value)))
    return tuple(tokens)


def _token_diff_opcodes(
    original: str,
    replacement: str,
) -> tuple[tuple[str, int, int, int, int], ...]:
    """Diff by whole words, returning character offsets for the editors.

    Character-level diffing highlighted the letters that happened to differ, so
    "recieve" -> "receive" lit up two loose characters and a reader could not
    see which word had changed. Matching whole tokens marks the word.
    """
    original_tokens = _diff_tokens(original)
    replacement_tokens = _diff_tokens(replacement)
    matcher = difflib.SequenceMatcher(
        None,
        [value for value, _, _ in original_tokens],
        [value for value, _, _ in replacement_tokens],
        autojunk=False,
    )
    opcodes: list[tuple[str, int, int, int, int]] = []
    for operation, i1, i2, j1, j2 in matcher.get_opcodes():
        # Collapse a token range back to the character range it covers. An
        # empty range keeps the insertion point so pure insertions still show.
        start = (
            original_tokens[i1][1]
            if i1 < len(original_tokens)
            else len(original)
        )
        end = original_tokens[i2 - 1][2] if i2 > i1 else start
        replacement_start = (
            replacement_tokens[j1][1]
            if j1 < len(replacement_tokens)
            else len(replacement)
        )
        replacement_end = (
            replacement_tokens[j2 - 1][2] if j2 > j1 else replacement_start
        )
        opcodes.append(
            (operation, start, end, replacement_start, replacement_end)
        )
    return tuple(opcodes)


def _bounded_review_units(
    text: str,
    spans: Sequence[tuple[int, int]],
    *,
    maximum_characters: int,
    maximum_words: int,
) -> tuple[tuple[int, int], ...]:
    """Split unusually long sentences at word boundaries before AI routing."""
    bounded: list[tuple[int, int]] = []
    for start, end in spans:
        cursor = start
        while cursor < end:
            chunk_end = min(end, cursor + maximum_characters)
            words = tuple(re.finditer(r"\S+", text[cursor:end]))
            if len(words) > maximum_words:
                chunk_end = min(chunk_end, cursor + words[maximum_words - 1].end())
            if chunk_end < end:
                whitespace = max(
                    text.rfind(" ", cursor + 1, chunk_end + 1),
                    text.rfind("\t", cursor + 1, chunk_end + 1),
                )
                if whitespace > cursor:
                    chunk_end = whitespace
            trimmed_end = chunk_end
            while trimmed_end > cursor and text[trimmed_end - 1].isspace():
                trimmed_end -= 1
            if trimmed_end <= cursor:
                trimmed_end = min(end, max(cursor + 1, chunk_end))
            bounded.append((cursor, trimmed_end))
            cursor = chunk_end
            while cursor < end and text[cursor].isspace():
                cursor += 1
    return tuple(bounded)


def _compact_line_fragment(text: str, span: tuple[int, int]) -> bool:
    value = text[span[0] : span[1]].strip()
    return bool(
        value
        and len(value) <= 100
        and len(value.split()) <= 12
        and re.search(r"[.!?][\"'\u201d\u2019)\]]*$", value) is None
    )


def _review_spans(
    text: str,
    *,
    maximum_sentences: int = 5,
    maximum_characters: int = _REVIEW_MAX_CHARACTERS,
    maximum_words: int = _REVIEW_MAX_WORDS,
) -> tuple[tuple[int, int, int], ...]:
    sentence_spans = _bounded_review_units(
        text,
        _sentence_spans(text),
        maximum_characters=maximum_characters,
        maximum_words=maximum_words,
    )
    if not sentence_spans:
        return ()
    reviews: list[tuple[int, int, int]] = []
    current: list[tuple[int, int]] = []
    for sentence in sentence_spans:
        gap = text[current[-1][1] : sentence[0]] if current else ""
        line_break = bool(current and ("\n" in gap or "\r" in gap))
        blank_line = bool(
            line_break
            and re.search(r"(?:\r\n|\r|\n)[ \t]*(?:\r\n|\r|\n)", gap)
        )
        # Two short unpunctuated lines used to be reviewed as one unit, on the
        # theory that they were one wrapped fragment. In a document they are
        # not: a line break in the captured text is there because the writer
        # pressed Enter. Reviewing them together means the replacement is one
        # run of text, and applying it silently deletes the break - essay 2's
        # title and subtitle came back fused into a single line, ten paragraphs
        # in and nine out.
        #
        # Kept only where the gap is not a real paragraph mark. Word hands UI
        # Automation a carriage return per paragraph, so in practice that means
        # this no longer fires there, which is the intent.
        compact_line_pair = bool(
            line_break
            and not blank_line
            and "\r" not in gap
            and "\n" not in gap
            and _compact_line_fragment(text, current[-1])
            and _compact_line_fragment(text, sentence)
        )
        paragraph_break = line_break and not compact_line_pair
        size_overload = bool(
            current
            and (
                sentence[1] - current[0][0] > maximum_characters
                or len(text[current[0][0] : sentence[1]].split()) > maximum_words
            )
        )
        dependent_fragment = bool(
            re.match(
                r"\s*(?:Because\b|While\b|Which\s+(?:caus|result|lead|made|was|were|"
                r"is|are|has|have|had)\w*\b)",
                text[sentence[0] : sentence[1]],
                re.IGNORECASE,
            )
        )
        literal_overload = bool(
            current
            and len(current) >= 2
            and not paragraph_break
            and not dependent_fragment
            and _review_literal_burden(text[current[0][0] : sentence[1]]) > 6
        )
        if size_overload:
            reviews.append((current[0][0], current[-1][1], len(current)))
            current = []
        elif (
            current
            and not paragraph_break
            and len(current) >= maximum_sentences
            and dependent_fragment
            and len(current) > 1
        ):
            reviews.append((current[0][0], current[-2][1], len(current) - 1))
            current = [current[-1]]
        elif literal_overload:
            reviews.append((current[0][0], current[-1][1], len(current)))
            current = []
        elif current and (paragraph_break or len(current) >= maximum_sentences):
            # Batches grow up to the sentence cap but never span a paragraph
            # break: the local models reliably keep single-paragraph
            # structure, while multi-paragraph requests kept merging them.
            reviews.append((current[0][0], current[-1][1], len(current)))
            current = []
        current.append(sentence)
    if current:
        reviews.append((current[0][0], current[-1][1], len(current)))
    return tuple(reviews)


def _sentence_spans(text: str) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    cursor = 0
    boundary_pattern = re.compile(
        r"[.!?]+[\"'\u201d\u2019)\]]*(?=\s|$)|[\r\n]+"
    )
    for boundary in boundary_pattern.finditer(text):
        if _is_nonterminal_abbreviation_boundary(text, boundary.start(), boundary.end()):
            continue
        left = cursor
        while left < boundary.end() and text[left].isspace():
            left += 1
        right = boundary.end()
        while right > left and text[right - 1].isspace():
            right -= 1
        if right > left:
            spans.append((left, right))
        cursor = boundary.end()
    left = cursor
    while left < len(text) and text[left].isspace():
        left += 1
    right = len(text)
    while right > left and text[right - 1].isspace():
        right -= 1
    if right > left:
        spans.append((left, right))
    if not spans and text.strip():
        start = len(text) - len(text.lstrip())
        end = len(text.rstrip())
        spans.append((start, end))
    return tuple(spans)


def _is_nonterminal_abbreviation_boundary(text: str, start: int, end: int) -> bool:
    if not text[start:end].startswith("."):
        return False
    prefix = text[:end].casefold()
    if re.search(
        r"(?<![a-z])(?:mr|mrs|ms|dr|prof|sr|jr|st)\.$",
        prefix,
    ):
        return True
    if not prefix.endswith(("a.m.", "p.m.", "e.g.", "i.e.")):
        return False
    following = text[end:].lstrip()
    return bool(following and following[:1].islower())


def _sentence_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    left = 0
    for match in re.finditer(r"[.!?]+[\"'\u201d\u2019)\]]*|[\r\n]+", text[:start]):
        left = match.end()
    # Trim the whitespace that separates this sentence from the one before,
    # but never walk past the edit being bounded. When the edit IS that
    # whitespace - a double space sitting between two sentences - this loop
    # skipped over it and returned a span beginning after it. The local offset
    # then went negative, Python sliced backwards to an empty string, and the
    # match guard below rejected an edit it had been handed out of range.
    #
    # Measured 2026-08-15: two characters at index 7833 of essay 20 in the
    # 500-percent corpus took the whole 25-section review to zero.
    while left < start and text[left].isspace():
        left += 1
    right = len(text)
    boundary = re.search(r"[.!?]+[\"'\u201d\u2019)\]]*|[\r\n]+", text[max(start, end) :])
    if boundary is not None:
        right = max(start, end) + boundary.end()
    # Same rule at the other end: trailing whitespace goes, the edit stays.
    while right > max(left, end) and text[right - 1].isspace():
        right -= 1
    if right <= left:
        return max(0, start), min(len(text), max(start + 1, end))
    return left, right


def _apply_sentence_suggestion(text: str, suggestion: object) -> str:
    if not isinstance(suggestion, _SentenceSuggestion):
        raise TypeError("Expected a full-sentence suggestion.")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]
    if digest != suggestion.source_digest:
        raise ValueError("Sentence suggestion is stale. Analyze the current draft again.")
    if text[suggestion.start : suggestion.end] != suggestion.original:
        raise ValueError("Sentence suggestion no longer matches the current draft.")
    return text[: suggestion.start] + suggestion.replacement + text[suggestion.end :]


def _shift_remaining_sentence_suggestions(
    updated: str,
    accepted: object,
    remaining: Sequence[object],
) -> tuple[_SentenceSuggestion, ...]:
    if not isinstance(accepted, _SentenceSuggestion):
        raise TypeError("Expected a full-sentence suggestion.")
    delta = len(accepted.replacement) - len(accepted.original)
    digest = hashlib.sha256(updated.encode("utf-8")).hexdigest()[:20]
    shifted: list[_SentenceSuggestion] = []
    for item in remaining:
        if not isinstance(item, _SentenceSuggestion):
            raise TypeError("Expected full-sentence suggestions.")
        if item.end <= accepted.start:
            start = item.start
            end = item.end
        elif item.start >= accepted.end:
            start = item.start + delta
            end = item.end + delta
        else:
            raise ValueError("Pending sentence suggestions overlap accepted text.")
        if updated[start:end] != item.original:
            raise ValueError("Pending sentence suggestion no longer matches the draft.")
        shifted.append(
            replace(
                item,
                start=start,
                end=end,
                source_digest=digest,
            )
        )
    return tuple(shifted)


def _apply_all_sentence_suggestions(text: str, suggestions: Sequence[object]) -> str:
    result = text
    sentence_suggestions = tuple(suggestions)
    for suggestion in sentence_suggestions:
        if not isinstance(suggestion, _SentenceSuggestion):
            raise TypeError("Expected full-sentence suggestions.")
        if suggestion.source_digest != hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]:
            raise ValueError("Sentence suggestions are stale. Analyze the current draft again.")
    for suggestion in sorted(sentence_suggestions, key=lambda item: item.start, reverse=True):
        if result[suggestion.start : suggestion.end] != suggestion.original:
            raise ValueError("Sentence suggestion no longer matches the current draft.")
        result = result[: suggestion.start] + suggestion.replacement + result[suggestion.end :]
    return result


def _category_name(suggestion: object) -> str:
    category = getattr(suggestion, "category", "")
    value = getattr(category, "value", category)
    normalized = str(value).strip().casefold()
    for name in FILTERS[1:]:
        if normalized == name.casefold():
            return name
    return "Clarity"
