"""The corpus must keep the shapes that a real assignment has.

On 2026-09-01 seven defects sat between Curtis's university assignment and a
correction, and the suite was green through every one: 2,527 tests passing, a
corpus mean of 99.4, 17 of 20 essays at 100. Both numbers were true and both
measured the wrong thing, because the gated corpus is twenty essays of plain
paragraphs. Every defect he hit lived in structure it has never contained.

These assertions are deliberately about STRUCTURE, not wording. A document whose
numbering is typed literally as "1." is ordinary text that both readers see, and
it would prove nothing: the defect only exists when Word owns the label.
"""
from __future__ import annotations

from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "test-corpus" / "document-shapes"


def _document_xml(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        return archive.read("word/document.xml").decode("utf-8")


def _parts(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as archive:
        return set(archive.namelist())


def test_the_corpus_exists_and_is_not_empty():
    assert CORPUS.is_dir(), f"{CORPUS} is missing; run make_document_shapes_corpus.py"
    assert len(sorted(CORPUS.glob("*.docx"))) >= 5


def test_at_least_one_document_carries_automatic_numbering():
    """numPr, not a typed '1.'. Word owning the label is the whole defect."""
    numbered = [
        path
        for path in sorted(CORPUS.glob("*.docx"))
        if "<w:numPr>" in _document_xml(path)
    ]
    assert numbered, "no document carries automatic numbering"
    for path in numbered:
        assert "word/numbering.xml" in _parts(path), (
            f"{path.name} references numbering it does not define"
        )


def test_a_numbered_document_has_more_than_one_item():
    """One item cannot show a drift that accumulates down the document."""
    path = CORPUS / "01_numbered_assignment.docx"
    assert _document_xml(path).count("<w:numPr>") >= 2


def test_a_document_ends_in_blank_paragraphs():
    """The digest defect: the capture kept trailing marks, the replacement trimmed.

    Most documents anyone saves end in a blank line, and none in the gated
    corpus did.
    """
    xml = _document_xml(CORPUS / "04_trailing_blank_lines.docx")
    body = xml.split("<w:body>", 1)[1]
    assert body.rstrip().endswith("<w:sectPr/></w:body></w:document>")
    assert "<w:p></w:p><w:p></w:p>" in body, "no empty paragraphs at the end"


def test_a_document_carries_a_nonbreaking_space():
    """Word reports one inconsistently to the two readers; nothing had one."""
    assert " " in _document_xml(CORPUS / "05_nonbreaking_space.docx")


def test_no_document_types_its_own_list_numbers():
    """A literal '1.' in the text would be ordinary text and prove nothing."""
    for path in sorted(CORPUS.glob("*.docx")):
        xml = _document_xml(path)
        for run in ("<w:t xml:space=\"preserve\">1.", "<w:t xml:space=\"preserve\">2."):
            assert run not in xml, f"{path.name} types its own list number"


def test_one_document_is_not_worth_twenty_points():
    """Curtis, 2026-09-02: "enlarge the shapes corpus so one document isn't 20
    points".

    With five documents the reliability metric moved in 20-point steps, and four
    repeat runs of the SAME corpus scored 80, 80, 60, 80 with a different
    document failing each time. A 20-point quantum on top of that noise cannot
    separate a real regression from a coin flip, so no experiment run against it
    was decidable. Twenty documents put the quantum at 5.
    """
    documents = sorted(CORPUS.glob("*.docx"))
    assert len(documents) >= 20, (
        f"{len(documents)} documents means {100 / max(len(documents), 1):.0f} "
        "points each; the metric cannot resolve a single failure"
    )


def test_every_named_shape_is_present():
    """The names are the record of which shapes have been measured at all."""
    expected = {
        "01_numbered_assignment",
        "02_bulleted_notes",
        "03_multi_level_outline",
        "04_trailing_blank_lines",
        "05_nonbreaking_space",
        "06_table",
        "07_tracked_changes",
        "08_field_result",
        "09_internal_hyperlink",
        "10_nested_bullets",
        "11_mixed_lists",
        "12_restarted_numbering",
        "13_smart_quotes",
        "14_dashes",
        "15_soft_hyphens",
        "16_long_document",
        "17_wall_of_text",
        "18_blank_lines_between",
        "19_tabs_and_spacing",
        "20_mixed_everything",
    }
    assert expected <= {path.stem for path in CORPUS.glob("*.docx")}


def test_the_table_is_a_real_table():
    """Word COM reports a cell end as BEL (U+0007) and accessibility does not.

    A table drawn with tab characters would be ordinary text on both sides and
    would prove nothing, exactly as a typed "1." proves nothing.
    """
    xml = _document_xml(CORPUS / "06_table.docx")
    assert "<w:tbl>" in xml and xml.count("<w:tc>") >= 6


def test_the_tracked_changes_document_carries_a_real_deletion():
    """A deletion is still in the file. Whether both readers agree it is part of
    the text is the question, so w:del has to be there and not just w:ins."""
    xml = _document_xml(CORPUS / "07_tracked_changes.docx")
    assert "<w:ins " in xml
    assert "<w:delText" in xml


def test_the_field_result_is_a_field_and_not_typed_text():
    xml = _document_xml(CORPUS / "08_field_result.docx")
    assert "<w:fldSimple" in xml


def test_the_restarted_list_uses_a_second_numbering_definition():
    """Two numbered lists in one document restart at 1.

    Word models that as a second numId over the same abstract definition. A
    reconstruction that keeps one counter per document gets every label in the
    second list wrong, and would still pass every other test here.
    """
    xml = _document_xml(CORPUS / "12_restarted_numbering.docx")
    assert 'w:numId w:val="1"' in xml
    assert 'w:numId w:val="3"' in xml


def test_the_typography_documents_carry_the_characters_they_name():
    assert "“" in _document_xml(CORPUS / "13_smart_quotes.docx")
    assert "–" in _document_xml(CORPUS / "14_dashes.docx")
    assert "—" in _document_xml(CORPUS / "14_dashes.docx")
    assert "­" in _document_xml(CORPUS / "15_soft_hyphens.docx")


def test_the_long_document_is_actually_long():
    """Nothing measured before reached a fifteenth review card in one session."""
    assert _document_xml(CORPUS / "16_long_document.docx").count("<w:p>") >= 20


def test_blank_paragraphs_appear_in_the_middle_not_only_at_the_end():
    """A zero-length review unit mid-run is a different case from a trailing one."""
    body = _document_xml(CORPUS / "18_blank_lines_between.docx").split("<w:body>", 1)[1]
    trailing = len(body.rstrip()) - len(body.rstrip().rstrip("<w:sectPr/></w:body></w:document>"))
    del trailing
    inner = body.split("<w:sectPr/>", 1)[0]
    first_empty = inner.find("<w:p></w:p>")
    assert first_empty > 0
    assert "<w:t" in inner[first_empty:], "every empty paragraph is at the end"


def test_the_tab_document_uses_a_tab_element_not_a_tab_character():
    """Word does not render a tab character inside w:t, so a literal one would
    have produced a document that looked like this shape and was not it."""
    xml = _document_xml(CORPUS / "19_tabs_and_spacing.docx")
    assert "<w:tab/>" in xml
    assert "\t" not in xml
