"""A review section is replaced as one run of text, so it must not hold a mark.

Measured on 2026-09-11, essay 16 of the error-riddled corpus, through Word as a
writer with the Clear tone: the document went in with 15 paragraphs and came out
with 14. Nothing was lost - source paragraph 10 was appended to paragraph 9 -
but a paragraph break the writer typed was deleted and cannot be clicked back.

The same essay, same 21 presses, Proofread only, came out 15 of 15. The tone
path is not the cause; it is the run that happened to hit the section.

Every layer in `review_spans` already guards this and says so at length:

  * `_sentence_spans` treats `[\r\n]+` as a hard boundary and explains, over
    eighteen lines, why VERTICAL TAB is deliberately not in that class.
  * `_review_spans` closes a batch on `paragraph_break` and records essay 2's
    title and subtitle coming back fused, ten paragraphs in and nine out.
  * `_sentence_bounds` stops at `[\r\n]+` at both ends.
  * `split_tone_batch` refuses a batched answer whose paragraph count moved,
    and promises "the worst case of a merged answer is today's behaviour,
    never a lost paragraph break".

`_group_sentence_suggestions` is the layer that assembles the final sections,
and it is the one that had no such guard. It must contain each edit it files
(`min(left, start)`, `max(right, end)`), so an edit whose own range carries a
paragraph mark drags the section across it, and the merge step then unions two
paragraph-safe spans into one that is not.

The edit that did it was the serial-comma rule, which took the mark into its
range without changing it:

    [5724,5765)  'disassembly\x0b and material identification\r'
              -> 'disassembly\x0b, and material identification\r'

Both ends are tested. The rule must not reach past the text it edits, and the
grouping must hold the line even when some future rule does.
"""
from __future__ import annotations

import sys
import pathlib
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from echo_link_app.review_spans import _group_sentence_suggestions
from echo_link_core.writing_suggestions import WritingSuggestionEngine


# The two paragraphs from essay 16, trimmed to the sentences that matter and
# joined the way Word hands a document to UI Automation: VERTICAL TAB for the
# Shift+Enter inside paragraph 9, CARRIAGE RETURN for the paragraph mark.
ESSAY_16_TAIL = (
    "At the same time, donated clothiing can overwhelm secondhand mirkets or "
    "shift disposal burdens to OTHER countries. "
    "A more responsible approach would design garments forrepeated repeated "
    "wear, repair, disassembly\v and material identification"
    "\r"
    "?? . Carreful evaluation can preserve genuine benefits while exposing "
    "claims that are unsupported or incomplete ; "
)


@dataclass(frozen=True)
class _Edit:
    """The shape `_group_sentence_suggestions` reads off a suggestion."""

    start: int
    end: int
    original: str
    replacement: str
    title: str = "Add the missing serial comma"
    explanation: str = ""
    category: str = "grammar"


def test_no_rule_edits_across_a_paragraph_mark():
    """An edit must not reach past the text it is actually changing."""
    edits = WritingSuggestionEngine().analyze(ESSAY_16_TAIL)

    straddling = [
        edit
        for edit in edits
        if "\r" in ESSAY_16_TAIL[edit.start : edit.end]
        or "\n" in ESSAY_16_TAIL[edit.start : edit.end]
    ]

    assert not straddling, (
        "these edits take a paragraph mark into their range: "
        + "; ".join(
            f"[{edit.start},{edit.end}) {edit.title!r} "
            f"{ESSAY_16_TAIL[edit.start:edit.end]!r} -> {edit.replacement!r}"
            for edit in straddling
        )
        + ". An edit that carries a mark forces the section around it to carry "
        "one too, and applying that section deletes the writer's break."
    )


def test_a_section_never_carries_a_paragraph_mark_even_for_a_greedy_edit():
    """The grouping holds the line whatever the rules hand it.

    Fixing only the rule above closes one instance. This closes the class: no
    section may span a mark, however the edit filed under it is shaped.
    """
    mark = ESSAY_16_TAIL.index("\r")
    greedy = _Edit(
        start=mark - len("disassembly\v and material identification"),
        end=mark + 1,
        original=ESSAY_16_TAIL[
            mark - len("disassembly\v and material identification") : mark + 1
        ],
        replacement="disassembly\v, and material identification\r",
    )

    sections = _group_sentence_suggestions(ESSAY_16_TAIL, [greedy])

    carrying = [
        section
        for section in sections
        if "\r" in section.original or "\n" in section.original
    ]
    assert not carrying, (
        "these sections span a paragraph mark: "
        + "; ".join(
            f"[{section.start},{section.end}) {section.original!r}"
            for section in carrying
        )
    )
    # And the paragraph on each side is still reachable as its own section.
    assert len(sections) >= 2
    assert all(section.end > section.start for section in sections)


def test_sections_still_cover_the_edit_they_were_built_for():
    """Clamping must not lose the correction, only the mark.

    This asserted only that SOME section changed and that each section quoted
    its own text, and an adversarial review showed it passing against the
    unfixed code - decoration, in the exact sense CLAUDE.md names. The
    assertion that matters is the function's own contract: an edit can always
    be placed, and placed in exactly one section. Only an edit that carries a
    mark may be refused, and it must be refused loudly.
    """
    edits = WritingSuggestionEngine().analyze(ESSAY_16_TAIL)
    sections = _group_sentence_suggestions(ESSAY_16_TAIL, edits)

    assert sections
    for section in sections:
        assert ESSAY_16_TAIL[section.start : section.end] == section.original

    for edit in edits:
        holding = [
            section
            for section in sections
            if section.start <= edit.start and edit.end <= section.end
        ]
        carries_mark = bool(
            set("\r\n") & set(ESSAY_16_TAIL[edit.start : edit.end])
        )
        if carries_mark:
            assert not holding, (
                f"edit [{edit.start},{edit.end}) carries a paragraph mark and "
                f"was filed under a section anyway, which is how the mark gets "
                f"written away"
            )
        else:
            assert len(holding) == 1, (
                f"edit [{edit.start},{edit.end}) landed in {len(holding)} "
                f"sections; the contract is exactly one"
            )


def test_an_edit_that_cannot_be_placed_is_dropped_loudly_and_privately():
    """The split makes this path fire in normal use for the first time.

    An edit that carries a mark now matches no section. Nothing downstream
    reads that, so if it were silent the correction would vanish with no trace
    for the writer, the harness or the suite. It is logged - and the log must
    not carry the writer's words, which is a rule this project states flatly.
    """
    import logging

    mark = ESSAY_16_TAIL.index("\r")
    greedy = _Edit(
        start=mark - 10,
        end=mark + 1,
        original=ESSAY_16_TAIL[mark - 10 : mark + 1],
        replacement="ification\r",
    )

    logger = logging.getLogger("echo_link_app.review_spans")
    records: list[logging.LogRecord] = []

    class _Catch(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Catch()
    logger.addHandler(handler)
    try:
        sections = _group_sentence_suggestions(ESSAY_16_TAIL, [greedy])
    finally:
        logger.removeHandler(handler)

    assert not any(
        section.start <= greedy.start and greedy.end <= section.end
        for section in sections
    )
    said = [r for r in records if "found no section" in r.getMessage()]
    assert said, "the correction was dropped in silence"
    for record in said:
        message = record.getMessage()
        assert str(greedy.original) not in message
        assert "material identification" not in message


def test_the_guard_holds_in_the_shape_the_app_actually_analyses():
    """Word gives CR; Qt gives the engine LF, and the two are not the same test.

    `show_capture` round-trips the capture through a Qt text widget, which
    rewrites every CR to LF before `_analyze_suggestions` ever sees it. The
    first version of these tests used CR throughout, so they exercised a shape
    the product never analyses - and at least one rule's separator class
    matches LF but not a bare CR, so a whole family of straddling edits was
    invisible to them.
    """
    lf_text = ESSAY_16_TAIL.replace("\r", "\n")
    edits = WritingSuggestionEngine().analyze(lf_text)
    sections = _group_sentence_suggestions(lf_text, edits)

    assert sections
    assert not [
        section
        for section in sections
        if set("\r\n") & set(section.original)
    ]
