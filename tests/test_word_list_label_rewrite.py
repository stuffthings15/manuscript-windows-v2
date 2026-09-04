"""A correction must survive the model not echoing Word's list label.

03_multi_level_outline failed section 4 of 7 on the 2026-09-03 baseline with
"refused a range covering an automatic list label". The reconstruction was not
at fault - proved offline, labels land at 66, 106, 144 and 181 and the rebuilt
text equals the capture character for character. The refusal comes from what the
model returns.

Reproduced offline, driving the real replacement against a fake Word:

    rewrite 'a. State the question in plain terms.'  -> applied
    rewrite 'State the question in plain terms.'     -> REFUSED
    rewrite 'A. State the question in plain terms.'  -> REFUSED

Accessibility reports the label, so the capture contains it, so every list item's
section BEGINS with a label the writer never typed and Word will re-supply on its
own. The old code trimmed that label off the edit only when the rewrite echoed it
character for character, which makes every correction in a list contingent on the
model copying "a. " exactly. That is fragile by construction and it is why single
level lists passed only sometimes.

The label is Word's, not the writer's. A rewrite that drops it has not proposed
deleting anything - it has returned the sentence, which is all it was ever
entitled to change.

WHAT IS STILL REFUSED, and must stay refused: a rewrite that opens with a
DIFFERENT label. Writing it over the body would leave the document reading
"a. 1. State the question", so where the intent cannot be read the edit is
declined. A refusal costs one correction; a duplicated label costs the document.
"""
from __future__ import annotations

import hashlib

import pytest

from echo_link_core import insertion
from echo_link_core.insertion import SelectionRecord, WindowSnapshot
from types import SimpleNamespace

PARAGRAPHS = (
    ("Essay Outline", ""),
    ("An outline i drafted quickly and have not checked .", ""),
    ("Introduction and the claim being makes.", "1."),
    ("State the question in plain language.", "a."),
    ("Explain why it matter to the reader.", "b."),
    ("Evidence, which is were the argument stands or falls.", "2."),
    ("This outline need another pass before it is useful.", ""),
    ("", ""),
)

CAPTURE = "\n".join(
    (label + " " if label else "") + text for text, label in PARAGRAPHS
).rstrip("\n")

SECTION = "a. State the question in plain language."


@pytest.fixture
def word(monkeypatch):
    """A Word document whose paragraphs tile Content.Text, as a real one does."""
    state = {"text": "".join(text + "\r" for text, _ in PARAGRAPHS), "assigned": []}

    class _Content:
        Start = 0

        @property
        def Text(self):
            return state["text"]

    class _Range:
        def __init__(self, start, end):
            self.start, self.end = start, end

        @property
        def Text(self):
            return state["text"][self.start : self.end]

        @Text.setter
        def Text(self, value):
            state["assigned"].append((self.start, self.end, value))
            state["text"] = (
                state["text"][: self.start] + value + state["text"][self.end :]
            )

    class _Document:
        Name = "Document1"
        Content = _Content()
        Paragraphs = [
            SimpleNamespace(
                Range=SimpleNamespace(
                    Text=text + "\r",
                    ListFormat=SimpleNamespace(ListString=label),
                )
            )
            for text, label in PARAGRAPHS
        ]

        def Activate(self):
            pass

        def Range(self, start, end):
            return _Range(start, end)

    class _Word:
        ActiveDocument = _Document()

        def Activate(self):
            pass

    monkeypatch.setattr(insertion, "_active_word_application", lambda: _Word())
    return state


def _replace(new_text, section=SECTION):
    at = CAPTURE.index(section)
    record = SelectionRecord(
        CAPTURE,
        WindowSnapshot(10, 20, "Document1 - Word", "OpusApp"),
        "document",
        hashlib.sha256(CAPTURE.encode("utf-8")).hexdigest(),
        (1, 2, 3),
        (10, 20, 500, 600),
        0,
        len(CAPTURE),
    )
    return insertion._replace_word_document_range(
        record,
        expected_document_text=CAPTURE,
        start=at,
        end=at + len(section),
        expected_text=section,
        new_text=new_text,
    )


def test_a_rewrite_that_echoes_the_label_still_applies(word):
    """The path that already worked. It must not regress."""
    assert _replace("a. State the question in plain terms.") is not False
    assert "State the question in plain terms." in word["text"]


def test_a_rewrite_that_drops_the_label_is_applied_to_the_sentence(word):
    """The failure. Word re-supplies the label, so dropping it deletes nothing."""
    assert _replace("State the question in plain terms.") is not False
    assert word["assigned"], "the correction was never written"
    assert "State the question in plain terms." in word["text"]


def test_the_document_does_not_gain_a_second_label(word):
    """Writing the rewrite over the label rather than the sentence would leave
    the paragraph reading "a. a. State ...". The label is Word's and lives
    outside Content.Text; it must appear nowhere in the text."""
    _replace("State the question in plain terms.")
    assert "a. " not in word["text"]
    assert word["text"].count("State the question in plain terms.") == 1


def test_a_rewrite_that_only_changes_the_label_case_is_applied_once(word):
    """"A. " is the same label shouted. Strip it rather than duplicating it."""
    assert _replace("A. State the question in plain terms.") is not False
    assert "A. State" not in word["text"]
    assert word["text"].count("State the question in plain terms.") == 1


def test_a_rewrite_opening_with_a_different_label_is_refused(word):
    """Ambiguous: the document would read "a. 1. State the question".

    A refusal costs one correction. A duplicated label costs the document, and
    the writer would have to find it by eye.
    """
    assert _replace("1. State the question in plain terms.") is False
    assert word["assigned"] == []


def test_a_section_spanning_two_list_items_is_still_refused(word):
    """A label in the MIDDLE of an edit cannot be expressed as one range."""
    spanning = CAPTURE[
        CAPTURE.index(SECTION) : CAPTURE.index("b. Explain why it matter to the reader.")
        + len("b. Explain why it matter to the reader.")
    ]
    assert _replace("something entirely different", section=spanning) is False
    assert word["assigned"] == []
