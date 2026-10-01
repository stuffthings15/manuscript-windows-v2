"""A whole-document capture must be refreshed after every applied correction.

THE DEFECT THAT COST ELEVEN EXPERIMENTS. `_replace_word_document_range` ended:

    if not record_covers_entire_document(record):
        ...rebuild the record with updated_text...
        return replace(record, ...)
    return True

So a document-scope capture - which is what the HUD actually takes - kept its
ORIGINAL pre-edit text for the whole review. Every applied correction pushed the
live document further from that stale capture.

It survives a few edits, which is what made it hard to see: the list-label
lookup re-synchronises at each label and tolerates a drift of `len(label) + 2`.
Then it does not. Measured on 03_multi_level_outline, 2026-09-04:

    Word automatic list labels not reconstructed: label 2 is not in the
    captured text near offset 111
    ... refused changed document text (range_chars=288 captured_chars=300)

288 against 300 is exactly the twelve characters of four three-character labels:
the reconstruction had given up entirely and returned nothing, so every
remaining section was refused. Three sections applied, then paragraphs 4, 5, 6
and 7 came back unchanged and the run stalled at 5 of 7.

That one line is why every reliability experiment scored 0.0 against a 0.0
baseline. Eleven experiments tested keystroke batching, insert attempts, settle
windows and recapture timing against a document that could not finish for a
reason no constant touches.

The comment beside the selection branch records this being found and fixed for
SELECTIONS on 2026-09-01. The document branch was left behind, and it is the
more common one.
"""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

from echo_link_core import insertion
from echo_link_core.insertion import SelectionRecord, WindowSnapshot

PARAGRAPHS = [
    ("Essay Outline", ""),
    ("An outline i drafted quickly and have not checked .", ""),
    ("Introduction and the claim being makes.", "1."),
    ("State the question in plain language.", "a."),
    ("Explain why it matter to the reader.", "b."),
    ("Evidence, which is were the argument stands or falls.", "2."),
    ("This outline need another pass before it is useful.", ""),
]
CAPTURE = "\n".join((label + " " if label else "") + text for text, label in PARAGRAPHS)


def _word(state, monkeypatch):
    """A Word whose paragraphs follow the live text, as the real one does."""

    def paragraphs():
        live = state["text"].split("\r")[:-1]
        return [
            SimpleNamespace(
                Range=SimpleNamespace(
                    Text=text + "\r",
                    ListFormat=SimpleNamespace(ListString=PARAGRAPHS[index][1]),
                )
            )
            for index, text in enumerate(live)
        ]

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

        @property
        def Content(self):
            return _Content()

        @property
        def Paragraphs(self):
            return paragraphs()

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


def _fresh(monkeypatch):
    return _word(
        {"text": "".join(text + "\r" for text, _ in PARAGRAPHS), "assigned": []},
        monkeypatch,
    )


def _record(text):
    return SelectionRecord(
        text,
        WindowSnapshot(10, 20, "Document1 - Word", "OpusApp"),
        "document",
        hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def _apply(record, sentence, replacement):
    at = record.text.index(sentence)
    return insertion._replace_word_document_range(
        record,
        expected_document_text=record.text,
        start=at,
        end=at + len(sentence),
        expected_text=sentence,
        new_text=replacement,
    )


def test_a_document_record_is_returned_refreshed(monkeypatch):
    """The line itself: it used to return a bare True and refresh nothing."""
    _fresh(monkeypatch)
    updated = _apply(
        _record(CAPTURE),
        "An outline i drafted quickly and have not checked .",
        "An outline I drafted quickly and have not checked.",
    )
    assert isinstance(updated, SelectionRecord), (
        "a whole-document capture was not refreshed after the edit"
    )
    assert "An outline I drafted quickly and have not checked." in updated.text


def test_three_corrections_in_a_row_all_apply(monkeypatch):
    """The failure as the writer meets it.

    Each correction is applied against the record the previous one returned,
    which is what the HUD does. With the stale capture the third was refused
    and every section after it came back unchanged.
    """
    state = _fresh(monkeypatch)
    record = _record(CAPTURE)

    edits = [
        (
            "An outline i drafted quickly and have not checked .",
            "An outline I drafted quickly and have not checked.",
        ),
        ("Introduction and the claim being makes.", "Introduction and the claim made."),
        (
            "State the question in plain language.",
            "State the question in plain terms, briefly.",
        ),
    ]
    for sentence, replacement in edits:
        applied = len(state["assigned"])
        record = _apply(record, sentence, replacement)
        assert isinstance(record, SelectionRecord), (
            f"refused after {applied} of {len(edits)} corrections"
        )

    assert len(state["assigned"]) == len(edits)
    for _sentence, replacement in edits:
        assert replacement in record.text


def test_the_refreshed_record_keeps_the_document_scope(monkeypatch):
    """It must stay a whole-document record, or the next call takes the
    selection path and compares against offsets it does not have."""
    _fresh(monkeypatch)
    updated = _apply(
        _record(CAPTURE),
        "Introduction and the claim being makes.",
        "Introduction and the claim made.",
    )
    assert updated.scope == "document"
    assert insertion.record_covers_entire_document(updated)


def test_offsets_are_not_half_set_on_a_document_record(monkeypatch):
    """SelectionRecord refuses one offset without the other, and a
    whole-document capture carries neither. Moving only the end is exactly the
    half-set state that invariant forbids, and it raised ValueError the first
    time this fix ran."""
    _fresh(monkeypatch)
    updated = _apply(
        _record(CAPTURE),
        "Introduction and the claim being makes.",
        "Introduction and the claim made.",
    )
    assert (updated.selection_start is None) == (updated.selection_end is None)
