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
from echo_link_core.windows_selection import _text_digest


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
