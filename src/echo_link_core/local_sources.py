from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .data_protection import UnsafePathError, validate_local_data_file


_ALLOWED_SUFFIXES = frozenset({".md", ".txt"})
_WORD_PATTERN = re.compile(r"[^\W_]+(?:['-][^\W_]+)*", re.UNICODE)


@dataclass(frozen=True, slots=True)
class SourceMatch:
    """One search hit with a file-and-line citation."""

    path: str
    line_number: int
    snippet: str
    score: int

    @property
    def citation(self) -> str:
        return f"{self.path}:{self.line_number}"


@dataclass(frozen=True, slots=True)
class ApprovalResult:
    """Result of approving one local source."""

    path: str
    indexed: bool
    reason: str = ""


@dataclass(frozen=True, slots=True)
class _IndexedDocument:
    path: str
    lines: tuple[str, ...]


class LocalSourceIndex:
    """Bounded, memory-only search over explicitly approved local text files."""

    def __init__(
        self,
        *,
        max_file_bytes: int = 5 * 1024 * 1024,
        max_snippet_chars: int = 500,
    ) -> None:
        if max_file_bytes < 1:
            raise ValueError("max_file_bytes must be positive.")
        if max_snippet_chars < 40:
            raise ValueError("max_snippet_chars must be at least 40.")
        self.max_file_bytes = int(max_file_bytes)
        self.max_snippet_chars = int(max_snippet_chars)
        self._documents: dict[str, _IndexedDocument] = {}

    @property
    def approved_paths(self) -> tuple[str, ...]:
        return tuple(sorted(self._documents, key=str.casefold))

    def approve(self, paths: Iterable[str | Path]) -> tuple[ApprovalResult, ...]:
        """Read only the supplied files and add valid text to the memory index."""

        return tuple(self._approve_one(path) for path in paths)

    def revoke(self, path: str | Path) -> bool:
        """Remove one approved document and all of its in-memory content."""

        key = _normalized_path(path, strict=False)
        return self._documents.pop(key, None) is not None

    def clear(self) -> None:
        """Drop all approved source content from memory."""

        self._documents.clear()

    def search(self, query: str, *, limit: int = 8) -> list[SourceMatch]:
        """Return ranked line matches with local path and line citations."""

        phrase = " ".join((query or "").casefold().split())
        terms = tuple(dict.fromkeys(_tokens(phrase)))
        if not phrase or not terms or limit < 1:
            return []

        matches: list[SourceMatch] = []
        for document in self._documents.values():
            for index, line in enumerate(document.lines):
                normalized_line = " ".join(line.casefold().split())
                line_tokens = _tokens(normalized_line)
                present = sum(1 for term in terms if term in line_tokens)
                if present == 0:
                    continue
                exact_bonus = 100 if phrase in normalized_line else 0
                all_terms_bonus = 25 if present == len(terms) else 0
                frequency = sum(line_tokens.count(term) for term in terms)
                score = exact_bonus + all_terms_bonus + (present * 10) + frequency
                matches.append(
                    SourceMatch(
                        path=document.path,
                        line_number=index + 1,
                        snippet=self._snippet(document.lines, index),
                        score=score,
                    )
                )

        matches.sort(key=lambda hit: (-hit.score, hit.path.casefold(), hit.line_number))
        return matches[: min(int(limit), 100)]

    def _approve_one(self, path: str | Path) -> ApprovalResult:
        candidate = Path(path).expanduser()
        supplied = str(candidate)
        try:
            resolved = validate_local_data_file(
                candidate,
                purpose="Local source",
            )
        except UnsafePathError as exc:
            reason = (
                "Network paths are not allowed."
                if "Network" in str(exc)
                else "Symbolic links and junctions are not allowed."
            )
            return ApprovalResult(supplied, False, reason)
        except (FileNotFoundError, OSError, RuntimeError):
            return ApprovalResult(supplied, False, "File not found or inaccessible.")

        resolved_text = str(resolved)
        if not resolved.exists():
            return ApprovalResult(resolved_text, False, "File not found or inaccessible.")
        if not resolved.is_file():
            return ApprovalResult(resolved_text, False, "Source is not a file.")
        if resolved.suffix.casefold() not in _ALLOWED_SUFFIXES:
            return ApprovalResult(resolved_text, False, "Only .txt and .md files are allowed.")

        try:
            if resolved.stat().st_size > self.max_file_bytes:
                return ApprovalResult(resolved_text, False, "File exceeds the size limit.")
            with resolved.open("rb") as stream:
                content = stream.read(self.max_file_bytes + 1)
        except OSError:
            return ApprovalResult(resolved_text, False, "File could not be read.")

        if len(content) > self.max_file_bytes:
            return ApprovalResult(resolved_text, False, "File exceeds the size limit.")
        try:
            text = _decode_text(content)
        except UnicodeError:
            return ApprovalResult(resolved_text, False, "File is binary or is not supported text.")

        lines = tuple(text.splitlines())
        if not any(line.strip() for line in lines):
            return ApprovalResult(resolved_text, False, "File is empty.")

        self._documents[resolved_text] = _IndexedDocument(resolved_text, lines)
        return ApprovalResult(resolved_text, True)

    def _snippet(self, lines: tuple[str, ...], index: int) -> str:
        start = max(0, index - 1)
        end = min(len(lines), index + 2)
        snippet = " ".join(part.strip() for part in lines[start:end] if part.strip())
        snippet = " ".join(snippet.split())
        if len(snippet) <= self.max_snippet_chars:
            return snippet
        return snippet[: self.max_snippet_chars - 3].rstrip() + "..."


def _decode_text(content: bytes) -> str:
    if not content:
        return ""
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        return content.decode("utf-16")
    if b"\x00" in content:
        raise UnicodeError("NUL byte found")
    return content.decode("utf-8-sig")


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(match.group(0).casefold() for match in _WORD_PATTERN.finditer(text))


def _normalized_path(path: str | Path, *, strict: bool) -> str:
    try:
        return str(Path(path).expanduser().resolve(strict=strict))
    except (OSError, RuntimeError):
        return str(Path(path).expanduser().absolute())
