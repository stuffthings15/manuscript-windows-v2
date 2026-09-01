"""The capture digest and the replacement read must agree on where a document ends.

On 2026-09-01 a writer accepted the first correction and every correction after
it was refused with "The document changed since it was captured", then the
review collapsed to "0 of 0". The document had not been touched: the log shows
the same live digest 29 seconds apart, `doc_chars=3518` both times, and
`range=600..3525` - a captured range ending seven characters past the end of the
live document.

Seven characters is the trailing paragraph marks. `_word_visible_text_and_spans`
trims trailing newlines before hashing; `_text_digest` did not, though its own
docstring promises it digests "the same way the replacement side reads it back".
Any document ending in an empty paragraph therefore produced two digests, and
the range guard correctly refused to write into a document it could not verify.
"""
from __future__ import annotations

import hashlib

from echo_link_core.insertion import (
    _word_automatic_list_labels,
    _word_visible_text_and_spans,
)
from echo_link_core.windows_selection import _text_digest, normalize_captured_text


def _text_normalized(text: str) -> str:
    """The capture text in the one form both sides agree on."""
    value = normalize_captured_text(text)
    value = value.replace(chr(13) + chr(10), chr(10)).replace(chr(13), chr(10))
    return value.rstrip(chr(10))


def _live_digest(word_com_text: str) -> str:
    visible, _ = _word_visible_text_and_spans(word_com_text)
    return hashlib.sha256(visible.encode("utf-8")).hexdigest()


def test_digests_agree_on_a_document_ending_in_empty_paragraphs():
    # Word hands COM a carriage return per paragraph mark, and a document that
    # ends with blank lines carries several of them after the last sentence.
    com_text = "First line.\rSecond line.\r\r\r\r\r\r\r"
    uia_text = "First line.\rSecond line.\r\r\r\r\r\r\r"
    assert _text_digest(uia_text) == _live_digest(com_text)


def test_digests_agree_on_a_document_with_no_trailing_marks():
    com_text = "First line.\rSecond line."
    assert _text_digest(com_text) == _live_digest(com_text)


def test_a_real_edit_still_changes_the_digest():
    # The trim must not blind the guard: an actual change to the text has to
    # still be refused, which is the whole point of the digest.
    captured = _text_digest("First line.\rSecond line.\r\r")
    edited = _live_digest("First line.\rSecond LINE.\r\r")
    assert captured != edited


def test_automatic_list_labels_are_reconstructed_from_com_text():
    """Word COM omits automatic numbering; UI Automation reports it as text.

    On 2026-09-01 `Unit 1 Discussion.docx` had two automatically numbered
    paragraphs, '1.' and '2.'. The capture read 3524 characters and COM read
    3518, the six being those labels with their separating tabs, and the first
    difference was at offset 643 where the capture held the '1' of a label and
    the document held the 'I' that begins the item. Every correction was
    refused and the review collapsed to "0 of 0".
    """
    # What COM returns: no numbering anywhere in the text.
    com_text = "Intro.\rIt starts here.\rIt continues.\r"
    # What accessibility returns, and therefore what the capture recorded.
    uia_text = "Intro.\r1.\tIt starts here.\r2.\tIt continues.\r"
    labels = {7: "1.\t", 23: "2.\t"}

    visible, spans = _word_visible_text_and_spans(com_text, labels)

    assert visible == _text_normalized(uia_text)
    assert _text_digest(uia_text) == hashlib.sha256(
        visible.encode("utf-8")
    ).hexdigest()
    # A label occupies no COM characters, so its span is empty. That is what
    # lets the replacement path refuse a range that would write over one.
    assert spans[7] == (7, 7)
    assert spans[8] == (7, 7)
    # Real text either side of a label still maps to its own COM offset.
    assert spans[0] == (0, 1)
    assert spans[10][1] - spans[10][0] == 1


def test_a_document_without_lists_is_unchanged_by_the_label_path():
    com_text = "First line.\rSecond line.\r"
    assert _word_visible_text_and_spans(com_text, {}) == (
        _word_visible_text_and_spans(com_text)
    )


class _FakeRange:
    def __init__(self, text, label):
        self.Text = text
        self.ListFormat = type("_ListFormat", (), {"ListString": label})()
        # Deliberately wrong, and deliberately present. Word's range
        # coordinates are a different space from indices into Content.Text: on
        # the failing document a paragraph whose Range.Start was 751 began at
        # 641 in the text, 110 characters out, so a label keyed by Range.Start
        # landed inside an unrelated word. Reading this attribute is the bug
        # this fixture exists to catch.
        self.Start = 10_000


class _FakeParagraph:
    def __init__(self, text, label=""):
        self.Range = _FakeRange(text, label)


class _FakeDocument:
    def __init__(self, paragraphs):
        self.Paragraphs = paragraphs


class _FakeContent:
    def __init__(self, text):
        self.Text = text
        self.Start = 0


def _document(*paragraphs):
    return _FakeDocument(list(paragraphs))


def test_label_offsets_accumulate_rather_than_trusting_range_start():
    paragraphs = _document(
        _FakeParagraph("Intro.\r"),
        _FakeParagraph("It starts here.\r", "1."),
        _FakeParagraph("It continues.\r", "2."),
    )
    content = _FakeContent("Intro.\rIt starts here.\rIt continues.\r")
    capture = "Intro.\n1.\tIt starts here.\n2.\tIt continues."

    assert _word_automatic_list_labels(paragraphs, content, capture) == {
        7: "1.\t",
        23: "2.\t",
    }


def test_the_separator_is_read_from_the_capture_not_assumed():
    """Three assumptions about the separator failed in a row on 2026-09-01.

    A tab was hard-coded and the capture held a space. Word's own
    ListLevel.TrailingCharacter was then read and reports wdTrailingTab for
    that very list, so trusting it would have been wrong in the same way:
    accessibility does not report what the list template stores. Only the
    capture knows what the capture contains.
    """
    paragraphs = _document(
        _FakeParagraph("Intro.\r"),
        _FakeParagraph("It starts here.\r", "1."),
    )
    content = _FakeContent("Intro.\rIt starts here.\r")

    spaced = _word_automatic_list_labels(
        paragraphs, content, "Intro.\n1. It starts here."
    )
    tabbed = _word_automatic_list_labels(
        paragraphs, content, "Intro.\n1.\tIt starts here."
    )

    assert spaced == {7: "1. "}
    assert tabbed == {7: "1.\t"}


def test_labels_are_refused_when_paragraphs_do_not_tile_the_text():
    """A missing label refuses the edit; a misplaced one would corrupt it."""
    paragraphs = _document(
        _FakeParagraph("Intro.\r"),
        _FakeParagraph("A paragraph that is not in the content.\r", "1."),
    )
    content = _FakeContent("Intro.\rSomething else entirely.\r")
    capture = "Intro.\n1.\tA paragraph that is not in the content."

    assert _word_automatic_list_labels(paragraphs, content, capture) == {}


def test_labels_are_refused_when_the_capture_does_not_hold_them():
    paragraphs = _document(
        _FakeParagraph("Intro.\r"),
        _FakeParagraph("It starts here.\r", "1."),
    )
    content = _FakeContent("Intro.\rIt starts here.\r")

    # A capture with no numbering in it at all cannot be aligned against.
    assert _word_automatic_list_labels(
        paragraphs, content, "Intro.\nIt starts here."
    ) == {}
    # And neither can a missing capture.
    assert _word_automatic_list_labels(paragraphs, content, "") == {}
