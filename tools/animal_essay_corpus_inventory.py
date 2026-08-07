"""Pure, deterministic inventory for the locked 20-essay Word corpus.

This module reads existing files only. It never extracts, copies, rewrites, or
deletes an essay, and it does not start Manuscript or a local AI runtime.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from io import BytesIO
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "test-corpus" / "animal-pet-20"
ARCHIVE = CORPUS / "source.zip"
README = CORPUS / "README.txt"
EXPECTED_ESSAY_COUNT = 20
EXPECTED_PARAGRAPH_COUNT = 10
LOCKED_ARCHIVE_SHA256 = (
    "c80a6fba0978596a464e1e9ff3c17d17d6a928fa56445a97c8e045a0eb9ff771"
)
DOCX_FORMAT = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
VISIBLE_TONES = (
    "Clear",
    "Improve",
    "Rephrase",
    "Shorten",
    "Friendly",
    "Formal",
    "Professional",
    "Academic",
    "Conversational",
    "Concise",
    "Detailed",
    "Persuasive",
    "Confident",
    "Casual",
    "Technical",
    "Simplified",
    "High-school level",
    "College level",
    "Diplomatic",
    "Empathetic",
    "Cooperative",
    "Assertive",
    "Caveman",
)
ALL_VISIBLE_MODES = ("Proofread", *VISIBLE_TONES)
PHYSICAL_HUD_MODES = ("Proofread",)

_WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_ESSAY_NAME = re.compile(r"^(?P<index>\d{2})_.+\.docx$")
_WORD = re.compile(r"\b[\w'-]+\b", re.UNICODE)


@dataclass(frozen=True, slots=True)
class EssayInventory:
    index: int
    page_id: str
    name: str
    path: str
    extension: str
    media_type: str
    size_bytes: int
    sha256: str
    archive_size_bytes: int
    archive_sha256: str
    archive_matches: bool
    paragraph_count: int
    word_count: int
    character_count: int
    title: str
    text: str

    def metadata(self) -> dict[str, object]:
        """Return serializable inventory data without duplicating essay text."""
        value = asdict(self)
        value.pop("text")
        return value


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _docx_paragraphs(value: bytes) -> tuple[str, ...]:
    with zipfile.ZipFile(BytesIO(value)) as document:
        xml = document.read("word/document.xml")
    body = ET.fromstring(xml).find(f"{_WORD_NS}body")
    if body is None:
        return ()
    paragraphs: list[str] = []
    for node in body.iter(f"{_WORD_NS}p"):
        pieces: list[str] = []
        for run in node.iter():
            if run.tag == f"{_WORD_NS}t":
                pieces.append(run.text or "")
            elif run.tag in (f"{_WORD_NS}tab", f"{_WORD_NS}br"):
                pieces.append(" ")
        text = "".join(pieces).strip()
        if text:
            paragraphs.append(text)
    return tuple(paragraphs)


def load_inventory(corpus: Path = CORPUS) -> tuple[EssayInventory, ...]:
    """Load all existing essays in stable numeric order and verify archive bytes."""
    corpus = corpus.resolve()
    archive_path = corpus / ARCHIVE.name
    with zipfile.ZipFile(archive_path) as archive:
        archived = {
            entry.filename: archive.read(entry)
            for entry in archive.infolist()
            if entry.filename.casefold().endswith(".docx")
        }

    records: list[EssayInventory] = []
    for path in sorted(corpus.glob("*.docx")):
        match = _ESSAY_NAME.fullmatch(path.name)
        if match is None:
            continue
        value = path.read_bytes()
        archived_value = archived.get(path.name, b"")
        paragraphs = _docx_paragraphs(value)
        text = "\n".join(paragraphs)
        records.append(
            EssayInventory(
                index=int(match.group("index")),
                page_id=path.stem,
                name=path.name,
                path=str(path.resolve()),
                extension=path.suffix.casefold(),
                media_type=DOCX_FORMAT,
                size_bytes=len(value),
                sha256=_sha256(value),
                archive_size_bytes=len(archived_value),
                archive_sha256=_sha256(archived_value),
                archive_matches=bool(archived_value and value == archived_value),
                paragraph_count=len(paragraphs),
                word_count=len(_WORD.findall(text)),
                character_count=len(text),
                title=paragraphs[0] if paragraphs else "",
                text=text,
            )
        )
    return tuple(sorted(records, key=lambda record: record.index))


def validate_inventory(
    records: tuple[EssayInventory, ...] | None = None,
    *,
    corpus: Path = CORPUS,
) -> tuple[str, ...]:
    records = records if records is not None else load_inventory(corpus)
    failures: list[str] = []
    archive = corpus.resolve() / ARCHIVE.name
    if not archive.is_file():
        failures.append("locked source.zip is missing")
    elif _sha256(archive.read_bytes()) != LOCKED_ARCHIVE_SHA256:
        failures.append("locked source.zip hash changed")
    if len(records) != EXPECTED_ESSAY_COUNT:
        failures.append(
            f"expected {EXPECTED_ESSAY_COUNT} essays, found {len(records)}"
        )
    if tuple(record.index for record in records) != tuple(
        range(1, EXPECTED_ESSAY_COUNT + 1)
    ):
        failures.append("essay numbering is not exactly 01 through 20")
    for record in records:
        if record.extension != ".docx" or record.media_type != DOCX_FORMAT:
            failures.append(f"{record.name} is not a Word DOCX file")
        if not record.archive_matches:
            failures.append(f"{record.name} differs from locked source.zip")
        if record.paragraph_count != EXPECTED_PARAGRAPH_COUNT:
            failures.append(
                f"{record.name} has {record.paragraph_count} paragraphs; "
                f"expected {EXPECTED_PARAGRAPH_COUNT}"
            )
    return tuple(failures)


def expected_case_matrix(
    records: tuple[EssayInventory, ...],
    modes: tuple[str, ...] = ALL_VISIBLE_MODES,
) -> tuple[tuple[str, str], ...]:
    """Return every essay/mode case required for complete visible coverage."""
    return tuple(
        (record.page_id, mode)
        for record in records
        for mode in modes
    )


def coverage_gaps(
    records: tuple[EssayInventory, ...],
    completed_cases: set[tuple[str, str]],
    modes: tuple[str, ...] = ALL_VISIBLE_MODES,
) -> tuple[tuple[str, str], ...]:
    completed = {
        (str(page_id).strip(), str(mode).strip())
        for page_id, mode in completed_cases
    }
    return tuple(
        case for case in expected_case_matrix(records, modes) if case not in completed
    )
