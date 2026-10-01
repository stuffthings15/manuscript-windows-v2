"""Word's ListString is not always the glyph accessibility renders.

Measured on test-corpus/document-shapes-open/10_nested_bullets.docx, 2026-09-08,
by reading the same document through both readers:

    level 1 bullets   Word ListString '•'   capture '•'   agree
    level 2 sub-bullet Word ListString 'o'      capture '◦'  DISAGREE

Word stores the sub-bullet as the ASCII letter o, which is how the Symbol-font
template records it, and accessibility renders it as U+25E6 WHITE BULLET.

The label search found labels 1 and 3 exactly and could not find label 2, and
the function returned {} - discarding all three, including the two that were
right. With no labels the capture's 201 characters were compared against Word's
194, every offset past position 69 was out by six, and the downstream text check
refused every correction on the document. Three runs of the `open` reliability
slice: one press each, outcome `partial`, identical every time, and the slice
scored 66.7 with a spread of 0.0.

Nothing was ever corrupted - the guard did its job. It refused corrections it
could have kept.

The repair is the doctrine this function already states twice for the SEPARATOR:
whatever produced the captured text is the only authority on what it contains.
Word's glyph is a hint, and when the hint is wrong the capture still knows.
"""
from __future__ import annotations

from types import SimpleNamespace

from echo_link_core import insertion

# The real document, paragraph for paragraph.
PARAGRAPHS = (
    "Nested Points",
    "A short list with sublevels, which i drafted quickly .",
    "The first main point, which need support.",
    "A supporting detail that is not finished.",
    "The second main point, witch is stronger.",
)

# What Word's ListFormat.ListString actually returned, measured.
WORD_REPORTS = ("", "", "•", "o", "•")

# What accessibility actually emitted, measured. Note the third entry.
CAPTURE_PREFIXES = ("", "", "• ", "◦ ", "• ")


def _document(paragraphs, labels):
    def _paragraph(text, label):
        return SimpleNamespace(
            Range=SimpleNamespace(
                Text=text + "\r",
                ListFormat=SimpleNamespace(ListString=label),
            )
        )

    content = SimpleNamespace(Text="".join(text + "\r" for text in paragraphs))
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


def test_a_disagreeing_glyph_does_not_discard_the_labels_that_agreed():
    """The exact failure: label 2 wrong, and all three thrown away."""
    document, content = _document(PARAGRAPHS, WORD_REPORTS)
    labels = insertion._word_automatic_list_labels(
        document, content, _capture(PARAGRAPHS, CAPTURE_PREFIXES)
    )
    assert labels, (
        "One label Word reported differently from the rendered glyph discarded "
        "every label in the document. Labels 1 and 3 were found exactly."
    )
    assert len(labels) == 3, f"expected three labels, reconstructed {len(labels)}"


def test_the_sub_bullet_is_taken_from_the_capture_not_from_word():
    """U+25E6, what was rendered - never 'o', what Word stores."""
    document, content = _document(PARAGRAPHS, WORD_REPORTS)
    labels = insertion._word_automatic_list_labels(
        document, content, _capture(PARAGRAPHS, CAPTURE_PREFIXES)
    )
    assert "◦ " in labels.values(), (
        "The sub-bullet was not reconstructed as the character the capture "
        "holds. Reconstructing Word's 'o' instead would put every later offset "
        "out by the difference."
    )
    assert "o " not in labels.values(), "Word's stored glyph was used verbatim"


def test_the_two_readers_agree_on_length_afterwards():
    """194 against 200 was the failure. They must come out equal."""
    document, content = _document(PARAGRAPHS, WORD_REPORTS)
    captured = _capture(PARAGRAPHS, CAPTURE_PREFIXES)
    labels = insertion._word_automatic_list_labels(document, content, captured)
    visible, _spans = insertion._word_visible_text_and_spans(content.Text, labels)
    assert len(visible) == len(captured), (
        f"the readers still disagree: {len(visible)} against {len(captured)}"
    )
    assert visible == captured


def test_a_label_the_capture_does_not_hold_at_all_still_refuses():
    """The safety property. A gap that is not a label must reconstruct nothing.

    This is what stops the repair becoming a licence to invent labels. If the
    capture carries a whole missing paragraph where a label should be, swallowing
    it would put every later offset out by its length - the same defect with a
    different cause - so the bound and the newline guard still refuse.
    """
    document, content = _document(PARAGRAPHS, WORD_REPORTS)
    # A capture whose sub-item is preceded by far more than a label.
    prefixes = ("", "", "• ", "an entire unexpected sentence sits here ", "• ")
    labels = insertion._word_automatic_list_labels(
        document, content, _capture(PARAGRAPHS, prefixes)
    )
    assert "an entire unexpected sentence sits here " not in labels.values(), (
        "a run of text far longer than any label was accepted as one"
    )
