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

from echo_link_core.insertion import _word_visible_text_and_spans
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
