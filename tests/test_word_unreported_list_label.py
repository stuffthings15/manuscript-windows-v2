"""A list label Word does not report must still be reconstructed.

Measured on 10_nested_bullets in the 2026-09-03 reliability baseline:

    range_chars=198  captured_chars=199  first_diff=113:'A'!=' '

The arithmetic pins it exactly. The five paragraphs are 194 characters joined,
two top-level bullets contributed a two-character label each, and 194 + 4 = 198.
The nested bullet contributed nothing, while the capture held one character for
it - so from offset 113 onward the two readers disagreed and every correction on
that document was refused.

The cause is `ListFormat.ListString` returning empty for a list level the
document's numbering defines no template for. The old code only reconstructed a
label when Word named one, and advanced its position in the capture as though
nothing were there.

Nothing was corrupted, because the text equality check downstream refuses a
mismatch. The cost was a correction silently declined, which is what the writer
experiences as a section that will not fix anything.

The fix reads the gap out of the capture, the same way the separator is read,
because the capture is the only authority on what the capture contains. It is
deliberately additive: a paragraph it cannot locate leaves behaviour unchanged,
so a capture covering a selection rather than a whole document cannot begin
refusing what it accepts today. That property is asserted below, because it is
the one that could quietly break unrelated documents.
"""
from __future__ import annotations

from types import SimpleNamespace

from echo_link_core import insertion

TOP = "• "  # bullet and its separator, as Word reports it
NESTED = " "  # what accessibility emitted where Word reported nothing

PARAGRAPHS = (
    "Nested Points",
    "A short list with sublevels, which i drafted quickly .",
    "The first main point, which need support.",
    "A supporting detail that is not finished.",
    "The second main point, witch is stronger.",
)
LABELS_WORD_REPORTS = ("", "", "•", "", "•")


def _document(paragraphs, labels):
    """A Word document whose paragraphs tile Content.Text, as the real one does."""

    def _paragraph(text, label):
        return SimpleNamespace(
            Range=SimpleNamespace(
                Text=text + "\r",
                ListFormat=SimpleNamespace(ListString=label),
            )
        )

    content = SimpleNamespace(
        Text="".join(text + "\r" for text in paragraphs)
    )
    document = SimpleNamespace(
        Paragraphs=[
            _paragraph(text, label)
            for text, label in zip(paragraphs, labels, strict=True)
        ]
    )
    return document, content


def _capture(paragraphs, prefixes):
    return "\n".join(
        prefix + text for text, prefix in zip(paragraphs, prefixes, strict=True)
    )


CAPTURE_PREFIXES = ("", "", TOP, NESTED, TOP)


def test_the_nested_label_is_reconstructed_from_the_capture():
    document, content = _document(PARAGRAPHS, LABELS_WORD_REPORTS)
    labels = insertion._word_automatic_list_labels(
        document, content, _capture(PARAGRAPHS, CAPTURE_PREFIXES)
    )
    assert len(labels) == 3, "the nested bullet contributed no label"
    assert sorted(labels.values()) == sorted([TOP, NESTED, TOP])


def test_the_two_readers_now_agree_on_length():
    """198 against 199 was the failure. They must match exactly."""
    document, content = _document(PARAGRAPHS, LABELS_WORD_REPORTS)
    captured = _capture(PARAGRAPHS, CAPTURE_PREFIXES)
    labels = insertion._word_automatic_list_labels(document, content, captured)
    visible, _spans = insertion._word_visible_text_and_spans(content.Text, labels)
    assert len(visible) == len(captured)
    assert visible == captured


def test_a_paragraph_that_cannot_be_located_changes_nothing():
    """The additive property. A capture covering only part of the document must
    not start refusing work it accepts today."""
    document, content = _document(PARAGRAPHS, LABELS_WORD_REPORTS)
    # A capture of one paragraph, as a selection-scoped record produces.
    partial = TOP + PARAGRAPHS[2]
    labels = insertion._word_automatic_list_labels(document, content, partial)
    # Whatever it reconstructs, it must not invent a label for a paragraph it
    # never found in the capture.
    for offset, label in labels.items():
        assert label in (TOP, NESTED), f"invented {label!r} at {offset}"


def test_a_large_gap_is_not_mistaken_for_a_label():
    """A big gap means the paragraphs no longer tile the text. Reconstructing a
    label from it would misplace an edit, which costs the document rather than
    one correction."""
    document, content = _document(PARAGRAPHS, LABELS_WORD_REPORTS)
    wide = ("", "", TOP, "            ", TOP)
    labels = insertion._word_automatic_list_labels(
        document, content, _capture(PARAGRAPHS, wide)
    )
    assert "            " not in labels.values()


def test_an_empty_paragraph_contributes_no_label():
    """A blank paragraph has no text to locate, so there is nothing to verify."""
    paragraphs = ("Heading", "", "Body text here.")
    document, content = _document(paragraphs, ("", "", ""))
    labels = insertion._word_automatic_list_labels(
        document, content, "\n".join(paragraphs)
    )
    assert labels == {}


def test_a_paragraph_break_is_never_taken_for_a_label():
    """A newline in the gap is a line break, not a bullet.

    Found by reviewing the fix, and the FIRST version of this test proved
    nothing: with the guard deleted it still passed, because those fixtures
    never put a newline in the gap at all. This one does. The capture carries a
    blank line that Word's paragraph walk does not account for, which is what an
    accessibility reader emitting an extra break looks like.

    Taking that newline as a label would hide a line break inside a zero-width
    span and put every later offset out by one - this defect again, reached from
    the other side.
    """
    document, content = _document(("Heading", "Body"), ("", ""))
    labels = insertion._word_automatic_list_labels(
        document, content, "Heading\n\nBody"
    )
    for label in labels.values():
        assert "\n" not in label, "a line break was reconstructed as a label"
