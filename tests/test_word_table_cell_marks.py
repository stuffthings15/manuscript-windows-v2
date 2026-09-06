"""A table must be correctable, and a cell boundary must never be overwritten.

Measured on the 2026-09-03 reliability baseline, where the arithmetic is exact
rather than inferred:

    06_table            COM 264 vs capture 248, delta 16
                        = 12 cell ends + 4 row ends
    20_mixed_everything COM 250 vs capture 244, delta  6
                        =  4 cell ends + 2 row ends

Word's COM `Content.Text` puts U+0007 at the end of every cell and again at the
end of every row. Accessibility reports none of them and substitutes nothing, so
the two readers disagreed by exactly the number of marks, every offset after the
first cell was wrong, and both documents failed every correction. This is the
same defect class as the automatic list labels that made an assignment
uncorrectable, in a structure no corpus contained until 2026-09-03.

Two separate properties are asserted here, because the fix has two halves and
only one of them is about offsets:

1. The marks are skipped, so the lengths agree and a table can be corrected.
2. A range that SPANS a mark is refused. Assigning text across a cell boundary
   does not rewrite two cells, it MERGES them and destroys the table. Getting
   the offsets right without this would turn a refused correction into a
   silently mangled document, which is far worse.
"""
from __future__ import annotations

import hashlib

from echo_link_core import insertion
from echo_link_core.insertion import SelectionRecord, WindowSnapshot

CELL = "\x07"

# One row of three cells is "a<cell>b<cell>c<cell><row>": three cell marks and
# a row mark, four marks per row, which is where 4 rows -> 16 comes from.
TABLE_DOCUMENT = (
    "Field Measurements\r"
    "The readings below was taken over three days .\r"
    f"Day{CELL}Reading{CELL}Note{CELL}{CELL}"
    f"Monday{CELL}14.2{CELL}the meter were recalibrated{CELL}{CELL}"
    f"Tuesday{CELL}15.8{CELL}no issues to report{CELL}{CELL}"
    f"Wednesday{CELL}13.9{CELL}their was light rain{CELL}{CELL}"
    "I will repeat these next week too confirm the trend.\r"
)


def test_cell_and_row_marks_are_skipped_so_the_two_readers_agree():
    """The whole defect, stated as the arithmetic that exposed it."""
    visible, spans = insertion._word_visible_text_and_spans(TABLE_DOCUMENT)
    marks = TABLE_DOCUMENT.count(CELL)
    assert marks == 16, "the fixture no longer matches the measured document"
    assert CELL not in visible
    assert len(spans) == len(visible)


def test_the_delta_that_refused_every_correction_is_gone():
    """264 against 248 was the failure. The gap must be the marks and nothing else."""
    visible, _spans = insertion._word_visible_text_and_spans(TABLE_DOCUMENT)
    # \r becomes \n one for one, so the only length change is the marks, minus
    # the trailing newlines the builder drops.
    raw_without_marks = TABLE_DOCUMENT.replace(CELL, "")
    expected = len(raw_without_marks.replace("\r", "\n").rstrip("\n"))
    assert len(visible) == expected


def test_text_inside_a_cell_maps_back_to_its_true_com_offsets():
    """Skipping a character is only correct if the spans still point at the
    right place. An off-by-one here is the whole bug, moved."""
    visible, spans = insertion._word_visible_text_and_spans(TABLE_DOCUMENT)
    needle = "their was light rain"
    at = visible.index(needle)
    raw_start = spans[at][0]
    raw_end = spans[at + len(needle) - 1][1]
    assert TABLE_DOCUMENT[raw_start:raw_end] == needle


def test_a_cell_mark_is_unsafe_to_overwrite():
    """Refusing is the point. Merging two cells is worse than not correcting."""
    assert CELL in insertion._WORD_OBJECT_ANCHORS


def _table_word(state):
    class _Content:
        Start = 0

        @property
        def Text(self):
            return state["text"]

    class _Range:
        def __init__(self, start, end):
            # Word Range positions count CR+BEL as one position, even
            # though Content.Text returns both characters.
            def raw_offset(position):
                native = 0
                raw = 0
                while native < position:
                    if state["text"][raw:raw + 2] == "\r\x07":
                        raw += 2
                        native += 1
                    else:
                        native += insertion._utf16_unit_count(state["text"][raw])
                        raw += 1
                return raw
            self.start, self.end = raw_offset(start), raw_offset(end)

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

        def Activate(self):
            pass

        def Range(self, start, end):
            return _Range(start, end)

    class _Word:
        ActiveDocument = _Document()

        def Activate(self):
            pass

    return _Word()


def _record_for(visible):
    return SelectionRecord(
        visible,
        WindowSnapshot(10, 20, "Document1 - Word", "OpusApp"),
        "document",
        hashlib.sha256(visible.encode("utf-8")).hexdigest(),
        (1, 2, 3),
        (10, 20, 500, 600),
        0,
        len(visible),
    )


def test_a_correction_inside_one_cell_is_written(monkeypatch):
    """The point of the fix: a table stops being uncorrectable."""
    state = {"text": TABLE_DOCUMENT, "assigned": []}
    monkeypatch.setattr(
        insertion, "_active_word_application", lambda: _table_word(state)
    )
    visible, _spans = insertion._word_visible_text_and_spans(TABLE_DOCUMENT)
    at = visible.index("their was light rain")

    result = insertion._replace_word_document_range(
        _record_for(visible),
        expected_document_text=visible,
        start=at,
        end=at + len("their was light rain"),
        expected_text="their was light rain",
        new_text="there was light rain",
    )

    assert result is not False
    assert state["assigned"], "the correction was never written"
    assert "there was light rain" in state["text"]
    assert state["text"].count(CELL) == 16, "a cell or row mark was destroyed"


def test_a_range_spanning_a_cell_boundary_is_refused(monkeypatch, caplog):
    """Two cells at once would merge them. The table must survive intact."""
    state = {"text": TABLE_DOCUMENT, "assigned": []}
    monkeypatch.setattr(
        insertion, "_active_word_application", lambda: _table_word(state)
    )
    visible, _spans = insertion._word_visible_text_and_spans(TABLE_DOCUMENT)
    # "13.9" then "their was light rain" sit either side of a cell mark, so a
    # range covering both crosses the boundary.
    at = visible.index("13.9")
    across = visible.index("their was light rain") + len("their was light rain")

    result = insertion._replace_word_document_range(
        _record_for(visible),
        expected_document_text=visible,
        start=at,
        end=across,
        expected_text=visible[at:across],
        new_text="13.9 there was light rain",
    )

    assert result is False
    assert state["assigned"] == [], "a cell boundary was overwritten"
    assert state["text"] == TABLE_DOCUMENT


def test_real_uia_cell_terminators_match_com_paragraph_cell_pairs():
    from echo_link_core.windows_selection import normalize_captured_text, _text_digest
    raw = "Title\rDay\r\x07Reading\r\x07\r\x07After table.\r"
    uia = raw.replace("\r\x07", "\x07")
    visible, _ = insertion._word_visible_text_and_spans(raw)
    captured = normalize_captured_text(uia).replace("\r", "\n").rstrip("\n")
    assert len(normalize_captured_text(uia)) == len(uia)
    assert captured == visible
    assert _text_digest(uia) == hashlib.sha256(visible.encode()).hexdigest()


def test_captured_uia_table_can_correct_one_cell_without_removing_boundaries(monkeypatch):
    from echo_link_core.windows_selection import normalize_captured_text
    raw = "Title\rDay\r\x07their was rain\r\x07\r\x07After table.\r"
    state = {"text": raw, "assigned": []}
    monkeypatch.setattr(insertion, "_active_word_application", lambda: _table_word(state))
    uia = raw.replace("\r\x07", "\x07")
    captured = normalize_captured_text(uia).replace("\r", "\n").rstrip("\n")
    at = captured.index("their was rain")
    assert insertion._replace_word_document_range(
        _record_for(captured), expected_document_text=captured, start=at,
        end=at + len("their was rain"), expected_text="their was rain", new_text="there was rain",
    ) is not False
    assert state["text"].count(CELL) == raw.count(CELL)
    assert "there was rain" in state["text"]


def test_native_offsets_count_cell_pairs_once_and_unicode_as_utf16():
    raw = "A\r\x07😀B"
    assert insertion._word_native_offset(raw, raw.index("B")) == 4


def test_a_cell_terminator_span_includes_both_protected_characters():
    raw = "A\r\x07B"
    visible, spans = insertion._word_visible_text_and_spans(raw)
    assert visible == "A\nB"
    assert raw[slice(*spans[1])] == "\r\x07"
