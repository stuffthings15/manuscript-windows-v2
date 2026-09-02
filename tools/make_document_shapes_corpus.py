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

WHAT THIS WRITES. Documents carrying the same kinds of error the error-riddled
corpus carries, in the structures the error-riddled corpus lacks: automatic
numbering, automatic bullets, multi-level numbering, trailing blank paragraphs,
and a non-breaking space. The errors are ordinary - subject-verb agreement,
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
</w:numbering>"""

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


DOCUMENTS = {
    "01_numbered_assignment": numbered_assignment,
    "02_bulleted_notes": bulleted_notes,
    "03_multi_level_outline": multi_level_outline,
    "04_trailing_blank_lines": trailing_blank_lines,
    "05_nonbreaking_space": nonbreaking_space_prose,
}


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
