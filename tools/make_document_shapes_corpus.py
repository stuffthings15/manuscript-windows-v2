"""Build a corpus of the document SHAPES that the gated corpus never had.

    .venv/Scripts/python.exe tools/make_document_shapes_corpus.py \
        --out test-corpus/document-shapes

WHY THIS EXISTS. On 2026-09-01 Curtis could not correct an ordinary university
assignment. Seven defects sat between his document and a correction, and the
suite was green through every one of them: 2,527 tests passing and a corpus mean
of 99.4 with 17 of 20 essays at 100.

Both numbers were true and both were measuring the wrong thing. The gated corpus
is twenty essays of plain paragraphs. No automatic lists. No trailing blank
lines. So the harness drove Word and the HUD exactly as a writer does - the
mechanism was real - over material no writer produces. Every defect he hit lived
in structure the corpus has never contained:

* Word COM omits automatic list numbering and UI Automation reports it, so on
  any document with a numbered list the capture and the replacement disagreed
  about every offset after the first item, and every correction was refused.
* The capture digest kept trailing paragraph marks and the replacement side
  trimmed them, so a document ending in a blank line - which is most documents
  anyone saves - hashed differently on the two sides while nobody had touched it.

Neither is exotic. A numbered list and a trailing blank line are what a homework
assignment looks like. They were simply absent from everything being measured.

WHAT THIS WRITES. Twenty documents carrying the same kinds of error the
error-riddled corpus carries, in the structures the error-riddled corpus lacks:
automatic numbering, automatic bullets, multi-level numbering, trailing blank
paragraphs, a non-breaking space, a table, tracked changes, a field result, a
hyperlink, nested and mixed lists, a list that restarts, smart quotes, en and em
dashes, soft hyphens, a long document, a wall of text, blank lines in the
middle, a tab, and one document with several of these at once.

TWENTY, not five, and the count is the point. Curtis, 2026-09-02: "enlarge the
shapes corpus so one document isn't 20 points". With five documents the metric
moved in 20-point steps, and four repeat runs of the same corpus scored 80, 80,
60, 80 with a different document failing each time. A 20-point quantum sitting
on top of that noise cannot separate a regression from a coin flip, so no
experiment run against it was decidable. Twenty documents put the quantum at 5.

Each added shape is also a candidate defect finder in its own right, which is
how the automatic numbering earned its place: it was added as coverage and
immediately exposed a defect that had made a whole assignment uncorrectable.

The errors are ordinary - subject-verb agreement,
homophones, run-together words, spacing before punctuation - so the existing
grader scores them without any new scoring rule.

The numbering is AUTOMATIC, which is the whole point and the part that is easy
to get wrong. A document with "1." typed literally into the text proves nothing:
that is ordinary text and both readers see it. It has to be a real numPr
reference into numbering.xml, so Word generates the label, COM's Range.Text
omits it and accessibility reports it. That difference is the defect.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from xml.sax.saxutils import escape
import zipfile

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
</Types>"""

RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

DOCUMENT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
</Relationships>"""

# Two definitions: decimal ("1.", "2.") and bullet. Both are real automatic
# numbering, so Word owns the label and COM's Range.Text does not contain it.
# numId 1 is decimal, numId 2 is bullet. Level 1 of the decimal definition gives
# a second level ("a.") for the multi-level document.
NUMBERING = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="{W}">
<w:abstractNum w:abstractNumId="0">
<w:multiLevelType w:val="multilevel"/>
<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/>
<w:lvlText w:val="%1."/><w:lvlJc w:val="left"/>
<w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl>
<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="lowerLetter"/>
<w:lvlText w:val="%2."/><w:lvlJc w:val="left"/>
<w:pPr><w:ind w:left="1440" w:hanging="360"/></w:pPr></w:lvl>
</w:abstractNum>
<w:abstractNum w:abstractNumId="1">
<w:multiLevelType w:val="hybridMultilevel"/>
<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/>
<w:lvlText w:val="&#8226;"/><w:lvlJc w:val="left"/>
<w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl>
</w:abstractNum>
<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
<w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>
<w:num w:numId="3"><w:abstractNumId w:val="0"/></w:num>
</w:numbering>"""

# numId 3 shares abstract definition 0 with numId 1, which is how Word models a
# SECOND list that restarts at 1. A document with two numbered lists is the case
# where a label reconstruction that assumes one counter per document silently
# produces the wrong labels for the second half.

NONBREAKING_SPACE = " "


def paragraph(text: str, *, num_id: int | None = None, level: int = 0) -> str:
    """One paragraph, optionally carrying an automatic list label."""
    properties = ""
    if num_id is not None:
        properties = (
            "<w:pPr><w:numPr>"
            f'<w:ilvl w:val="{level}"/><w:numId w:val="{num_id}"/>'
            "</w:numPr></w:pPr>"
        )
    run = (
        f'<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r>'
        if text
        else ""
    )
    return f"<w:p>{properties}{run}</w:p>"


def document_xml(paragraphs: list[str]) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W}"><w:body>{"".join(paragraphs)}'
        "<w:sectPr/></w:body></w:document>"
    )


def write_docx(paragraphs: list[str], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("_rels/.rels", RELS)
        archive.writestr("word/_rels/document.xml.rels", DOCUMENT_RELS)
        archive.writestr("word/numbering.xml", NUMBERING)
        archive.writestr("word/document.xml", document_xml(paragraphs))


def run_xml(text: str) -> str:
    return f'<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def table(rows: list[list[str]]) -> str:
    """A real Word table.

    Word COM reports a cell end and a row end as BEL (U+0007) in Content.Text
    and accessibility does not, so a table is the same class of coordinate
    mismatch as the automatic list labels that made a whole assignment
    uncorrectable. Nothing in any corpus has ever contained one.
    """
    grid = "".join('<w:gridCol w:w="4000"/>' for _ in rows[0])
    body = ""
    for cells in rows:
        body += (
            "<w:tr>"
            + "".join(
                f"<w:tc><w:tcPr/><w:p>{run_xml(cell)}</w:p></w:tc>" for cell in cells
            )
            + "</w:tr>"
        )
    return f"<w:tbl><w:tblPr/><w:tblGrid>{grid}</w:tblGrid>{body}</w:tbl>"


def tracked(text: str, *, deleted: str = "") -> str:
    """A paragraph carrying a tracked insertion and a tracked deletion.

    A reviewed document is full of these and the capture has never seen one. A
    deletion is still in the file, so whether the two readers agree it is part
    of the text is exactly the question.
    """
    inserted = (
        '<w:ins w:id="101" w:author="Reviewer" w:date="2026-09-02T00:00:00Z">'
        f"{run_xml(text)}</w:ins>"
    )
    removed = (
        '<w:del w:id="102" w:author="Reviewer" w:date="2026-09-02T00:00:00Z">'
        f'<w:r><w:delText xml:space="preserve">{escape(deleted)}</w:delText></w:r>'
        "</w:del>"
        if deleted
        else ""
    )
    return f"<w:p>{inserted}{removed}</w:p>"


def field_paragraph(before: str, instruction: str, shown: str, after: str) -> str:
    """A field result, which COM reports as text and accessibility may not."""
    return (
        f"<w:p>{run_xml(before)}"
        f'<w:fldSimple w:instr="{escape(instruction)}">{run_xml(shown)}</w:fldSimple>'
        f"{run_xml(after)}</w:p>"
    )


def anchored_link(before: str, text: str, after: str) -> str:
    """An internal hyperlink. It needs no relationship part, unlike an external
    one, so the document stays a single self-contained package."""
    return (
        f"<w:p>{run_xml(before)}"
        f'<w:hyperlink w:anchor="top">{run_xml(text)}</w:hyperlink>'
        f"{run_xml(after)}</w:p>"
    )


def numbered_assignment() -> list[str]:
    """The shape of Curtis's assignment: prose, then an automatic numbered list.

    Two numbered items, because one item cannot show a drift that accumulates.
    """
    return [
        paragraph("Unit 1 Discussion"),
        paragraph(
            "From a first-person point of view, their are two claims i want to "
            "examine before the seminar meets ."
        ),
        paragraph(
            "It is a mistake too assume that every reader shares the same "
            "background, and the assumption effects how a argument lands.",
            num_id=1,
        ),
        paragraph(
            "North American readers in particular expects the evidence first, "
            "which change how the paragraph should be arranged.",
            num_id=1,
        ),
        paragraph(
            "Both points needs more support then I have given them here."
        ),
        paragraph(""),
        paragraph(""),
    ]


def bulleted_notes() -> list[str]:
    """Automatic bullets, whose label is a character Word owns, not a number."""
    return [
        paragraph("Reading Notes"),
        paragraph("These notes was taken during the second lecture ."),
        paragraph(
            "the speaker argued that context matter more then vocabulary",
            num_id=2,
        ),
        paragraph(
            "she also said that a summery is not the same as a analysis",
            num_id=2,
        ),
        paragraph(
            "I need too rewrite these before they are any use to me.",
        ),
        paragraph(""),
    ]


def multi_level_outline() -> list[str]:
    """Two numbering levels, so a label is not always the same width."""
    return [
        paragraph("Essay Outline"),
        paragraph("An outline i drafted quickly and have not checked ."),
        paragraph("Introduction and the claim being makes.", num_id=1),
        paragraph("State the question in plain language.", num_id=1, level=1),
        paragraph("Explain why it matter to the reader.", num_id=1, level=1),
        paragraph("Evidence, which is were the argument stands or falls.", num_id=1),
        paragraph("This outline need another pass before it is useful."),
        paragraph(""),
    ]


def trailing_blank_lines() -> list[str]:
    """Plain prose that simply ends in blank paragraphs, as saved documents do.

    No list at all, so a failure here is the digest and not the numbering.
    """
    return [
        paragraph("Weekly Reflection"),
        paragraph(
            "This week i learned that a draft is not finished when it stops "
            "being embarassing ."
        ),
        paragraph(
            "The revision took longer then the writing, and it effected how "
            "much time I had for the reading."
        ),
        paragraph(
            "Next week I will start early, because the deadline do not move."
        ),
        paragraph(""),
        paragraph(""),
        paragraph(""),
    ]


def nonbreaking_space_prose() -> list[str]:
    """A non-breaking space, which Word reports inconsistently to the two readers.

    `windows_selection.normalize_captured_text` exists for exactly this and
    carries the scar: one document differed by a single character between the
    two reads, every offset after it was out by one, and three essays of a
    twenty-essay run died. Nothing in the corpus contained one.
    """
    return [
        paragraph("Field Report"),
        paragraph(
            f"The site is near Bakersfield{NONBREAKING_SPACE}, United States, "
            "and the team were their for two days ."
        ),
        paragraph(
            "We recorded the readings each morning, and they was consistent "
            "accross the whole period."
        ),
        paragraph(""),
    ]


def table_document() -> list[str]:
    return [
        paragraph("Field Measurements"),
        paragraph("The readings below was taken over three days ."),
        table(
            [
                ["Day", "Reading", "Note"],
                ["Monday", "14.2", "the meter were recalibrated"],
                ["Tuesday", "15.8", "no issues to report"],
                ["Wednesday", "13.9", "their was light rain"],
            ]
        ),
        paragraph("I will repeat these next week too confirm the trend."),
        paragraph(""),
    ]


def tracked_changes_document() -> list[str]:
    return [
        paragraph("Draft With Comments"),
        paragraph("This paragraph have not been reviewed yet ."),
        tracked(
            "The reviewer added this sentence and it contain an error.",
            deleted="This sentence was removed by the reviewer.",
        ),
        paragraph("The conclusion still need work before it is submitted."),
        paragraph(""),
    ]


def field_document() -> list[str]:
    return [
        paragraph("Report With Fields"),
        field_paragraph(
            "This report begins on page ", " PAGE ", "1", " of the bundle ."
        ),
        paragraph("The figures was checked twice, and they are consistant."),
        paragraph(""),
    ]


def hyperlink_document() -> list[str]:
    return [
        paragraph("Linked Notes"),
        anchored_link("See ", "the summary above", " for the full argument ."),
        paragraph("Their are three points that still needs support."),
        paragraph(""),
    ]


def nested_bullets() -> list[str]:
    """Bullets with a sublevel, so the label width changes within one list."""
    return [
        paragraph("Nested Points"),
        paragraph("A short list with sublevels, which i drafted quickly ."),
        paragraph("The first main point, which need support.", num_id=2),
        paragraph("A supporting detail that is not finished.", num_id=2, level=1),
        paragraph("The second main point, witch is stronger.", num_id=2),
        paragraph(""),
    ]


def mixed_lists() -> list[str]:
    """A numbered list and a bulleted list in one document.

    Two label shapes at once: a digit and a stop, then a bullet character. A
    reconstruction that matches one pattern gets the other wrong.
    """
    return [
        paragraph("Mixed Lists"),
        paragraph("Numbered items followed by bulleted ones ."),
        paragraph("The first numbered item have an error.", num_id=1),
        paragraph("The second numbered item is fine."),
        paragraph("A bulleted item that need a comma.", num_id=2),
        paragraph("Another bullet, witch is misspelled.", num_id=2),
        paragraph(""),
    ]


def restarted_numbering() -> list[str]:
    """Two separate numbered lists, so the counter restarts mid-document."""
    return [
        paragraph("Two Separate Lists"),
        paragraph("The first list covers causes ."),
        paragraph("Rainfall, witch was heavier then usual.", num_id=1),
        paragraph("Drainage that were already blocked.", num_id=1),
        paragraph("The second list covers effects."),
        paragraph("Flooding in the lower streets.", num_id=3),
        paragraph("Damage that have not been costed.", num_id=3),
        paragraph(""),
    ]


def smart_quotes() -> list[str]:
    """Curly quotation marks, which the two readers may normalise differently."""
    return [
        paragraph("Quoted Material"),
        paragraph(
            "The author wrote “the evidence is thin” and i agree with "
            "them ."
        ),
        paragraph("She called it ‘a first attempt’, witch seems fair."),
        paragraph(""),
    ]


def dashes_document() -> list[str]:
    """En and em dashes, which autocorrect produces and plain text does not."""
    return [
        paragraph("Punctuation Practice"),
        paragraph(
            "The range – roughly ten to twelve – were wider then "
            "expected ."
        ),
        paragraph(
            "One more case—the last one—still need to be considered."
        ),
        paragraph(""),
    ]


def soft_hyphens() -> list[str]:
    """Soft hyphens, which a reader never sees and an offset count always does."""
    return [
        paragraph("Typesetting Notes"),
        paragraph(
            "This para­graph carries soft hy­phens that a reader never "
            "sees ."
        ),
        paragraph("They should not effect how the sentence is corrected."),
        paragraph(""),
    ]


def long_document() -> list[str]:
    """Twenty paragraphs, to measure whether behaviour degrades with length.

    Every earlier document is under ten sections, so nothing has ever measured
    what happens on the fifteenth review card of one session.
    """
    blocks = [paragraph("A Longer Piece"), paragraph("An essay of some length ,")]
    for index in range(1, 19):
        blocks.append(
            paragraph(
                f"Paragraph {index} makes a point that need support, and it "
                "contain at least one error witch should be corrected."
            )
        )
    blocks.append(paragraph(""))
    return blocks


def wall_of_text() -> list[str]:
    """One paragraph of many sentences, the shape sectioning has to split."""
    return [
        paragraph("One Long Paragraph"),
        paragraph(
            "This is a single paragraph that runs on for several sentences . "
            "It have no breaks at all. Their is no white space to help a "
            "reader. The corrector must still find each error in turn. Its a "
            "shape that writers produce more often then anyone admits."
        ),
        paragraph(""),
    ]


def blank_lines_between() -> list[str]:
    """Empty paragraphs in the MIDDLE, not only at the end.

    `04_trailing_blank_lines` covers the end. An empty paragraph between two
    full ones is a zero-length review unit in the middle of the run.
    """
    return [
        paragraph("Spaced Out"),
        paragraph("The first paragraph, witch is short ."),
        paragraph(""),
        paragraph(""),
        paragraph("The second paragraph, after two empty ones."),
        paragraph(""),
        paragraph("The third, which have its own error."),
        paragraph(""),
    ]


def tabs_and_spacing() -> list[str]:
    """A real tab element and doubled spaces, which normalisation must not eat.

    The tab is `<w:tab/>`, not a tab character inside `<w:t>`. Word does not
    render the latter, so a literal tab would have produced a document that
    looked like this shape and was not it.
    """
    tabbed = (
        "<w:p><w:r><w:tab/>"
        '<w:t xml:space="preserve">'
        + escape("This one begins with a tab, witch is unusual.")
        + "</w:t></w:r></w:p>"
    )
    return [
        paragraph("Spacing"),
        paragraph("This line  has  doubled  spaces and it need fixing ."),
        tabbed,
        paragraph(""),
    ]


def mixed_everything() -> list[str]:
    """Every shape at once, because defects in this class compound.

    The numbered-list defect only became visible when a list met a trailing
    blank line in the same document.
    """
    return [
        paragraph("Everything At Once"),
        paragraph("A document that combines the shapes above ."),
        paragraph("A numbered item that have an error.", num_id=1),
        paragraph("A bulleted item, witch is misspelled.", num_id=2),
        table([["Item", "Value"], ["First", "their are two"]]),
        paragraph(
            "A closing paragraph with a “quote” and a – dash "
            "– that need checking."
        ),
        paragraph(""),
        paragraph(""),
    ]


DOCUMENTS = {
    "01_numbered_assignment": numbered_assignment,
    "02_bulleted_notes": bulleted_notes,
    "03_multi_level_outline": multi_level_outline,
    "04_trailing_blank_lines": trailing_blank_lines,
    "05_nonbreaking_space": nonbreaking_space_prose,
    "06_table": table_document,
    "07_tracked_changes": tracked_changes_document,
    "08_field_result": field_document,
    "09_internal_hyperlink": hyperlink_document,
    "10_nested_bullets": nested_bullets,
    "11_mixed_lists": mixed_lists,
    "12_restarted_numbering": restarted_numbering,
    "13_smart_quotes": smart_quotes,
    "14_dashes": dashes_document,
    "15_soft_hyphens": soft_hyphens,
    "16_long_document": long_document,
    "17_wall_of_text": wall_of_text,
    "18_blank_lines_between": blank_lines_between,
    "19_tabs_and_spacing": tabs_and_spacing,
    "20_mixed_everything": mixed_everything,
}

# Shapes deliberately NOT here, so their absence is a recorded decision rather
# than an oversight. Each needs an OOXML part this single-file builder does not
# write, and a half-built one would look like a shape and prove nothing:
# footnotes and endnotes (footnotes.xml), headers and footers (header1.xml plus
# a sectPr reference), comments (comments.xml), text boxes (a drawing with a
# txbxContent), and an external hyperlink (a relationship in document.xml.rels).
# They are A1, A2, A11 and A13 in the experiment plan and want their own commit.


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("test-corpus/document-shapes"))
    arguments = parser.parse_args(argv)

    for name, build in DOCUMENTS.items():
        destination = arguments.out / f"{name}.docx"
        paragraphs = build()
        write_docx(paragraphs, destination)
        print(f"{name}.docx  ({len(paragraphs)} paragraphs)")  # noqa: T201

    print(f"\nwrote {len(DOCUMENTS)} documents to {arguments.out}")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
