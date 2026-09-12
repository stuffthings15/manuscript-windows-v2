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
    """Clamping must not lose the correction, only the mark."""
    edits = WritingSuggestionEngine().analyze(ESSAY_16_TAIL)
    sections = _group_sentence_suggestions(ESSAY_16_TAIL, edits)

    assert sections
    changed = [section for section in sections if section.has_changes]
    assert changed, "every correction for this text was dropped"
    for section in sections:
        assert ESSAY_16_TAIL[section.start : section.end] == section.original
