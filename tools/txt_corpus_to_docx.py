"""Turn a plain-text corpus into .docx so the Word harnesses can read it.

`tools/word_essay_session.py` and `tools/run_essays_batch.py` glob `*.docx`,
because correction quality is measured through Word and the HUD and never
headlessly. A corpus that arrives as .txt is invisible to both until it is
converted, and converting it by hand in Word is twenty documents of mouse
work that nobody will repeat.

Two decisions worth stating, because getting either wrong changes what is
being measured rather than just how it looks:

* A blank line separates paragraphs. The error-riddled corpus also injects
  stray line breaks *inside* a paragraph as one of its defects, and those
  become real line breaks (`w:br`) inside one Word paragraph, not paragraph
  splits. Splitting on them would hand the app fragments that begin
  mid-sentence, which is a harder and different job than the one it does.
* The first block is the essay title and stays a paragraph of its own, which
  is what `animal_essay_corpus_probe.load_essays` assumes and what a reader
  actually sees.

No third-party package: python-docx is not in this environment and the local
-only rule means not adding one for a conversion this small. A .docx is a zip
of three XML parts and Word opens exactly this.

    .venv/Scripts/python.exe tools/txt_corpus_to_docx.py \
        --source test-corpus/error-riddled-20 \
        --out test-corpus/error-riddled-20-docx
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
from xml.sax.saxutils import escape
import zipfile

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

BLANK_LINE = re.compile(r"\r?\n[ \t]*\r?\n")


def blocks(raw: str) -> list[str]:
    return [block for block in BLANK_LINE.split(raw) if block.strip()]


def paragraph_xml(block: str) -> str:
    """One Word paragraph, keeping the block's own line breaks as line breaks."""
    lines = block.strip().splitlines()
    runs = []
    for index, line in enumerate(lines):
        if index:
            runs.append("<w:r><w:br/></w:r>")
        # xml:space matters: these essays carry deliberate double spaces and
        # trailing spaces, and Word collapses them without it.
        runs.append(
            f'<w:r><w:t xml:space="preserve">{escape(line)}</w:t></w:r>'
        )
    return f"<w:p>{''.join(runs)}</w:p>"


def document_xml(raw: str) -> str:
    body = "".join(paragraph_xml(block) for block in blocks(raw))
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W}"><w:body>{body}'
        "<w:sectPr/></w:body></w:document>"
    )


def write_docx(raw: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("_rels/.rels", RELS)
        archive.writestr("word/document.xml", document_xml(raw))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    sources = sorted(
        path
        for path in args.source.glob("*.txt")
        if not path.name.startswith("~$") and path.stem.casefold() != "readme"
    )
    if not sources:
        parser.error(f"no .txt essays under {args.source}")

    for source in sources:
        raw = source.read_text(encoding="utf-8")
        destination = args.out / f"{source.stem}.docx"
        write_docx(raw, destination)
        print(f"{source.name} -> {destination.name} ({len(blocks(raw))} paragraphs)")

    print(f"\nwrote {len(sources)} documents to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
