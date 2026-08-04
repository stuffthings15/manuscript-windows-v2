from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .data_protection import UnsafePathError, validate_local_data_file


SUPPORTED_EXTENSIONS = frozenset({".md", ".txt"})
WORD_PATTERN = re.compile(r"[^\W_]+(?:['\u2019-][^\W_]+)*", re.UNICODE)


@dataclass(frozen=True, slots=True)
class PassageMatch:
    source_path: Path
    query_passage: str
    source_passage: str
    similarity: float
    word_count: int
    query_word_start: int
    source_word_start: int


@dataclass(frozen=True, slots=True)
class SourceComparison:
    source_path: Path
    similarity: float
    matched_word_count: int
    matches: tuple[PassageMatch, ...]


@dataclass(frozen=True, slots=True)
class SkippedSource:
    path: Path
    reason: str


@dataclass(frozen=True, slots=True)
class PlagiarismReport:
    similarity: float
    query_word_count: int
    scanned_files: tuple[Path, ...]
    sources: tuple[SourceComparison, ...]
    skipped_sources: tuple[SkippedSource, ...]

    @property
    def matches(self) -> tuple[PassageMatch, ...]:
        return tuple(match for source in self.sources for match in source.matches)

    def as_text(self) -> str:
        lines = [
            f"Local similarity: {self.similarity:.1%}",
            f"Words checked: {self.query_word_count}",
            f"Local files scanned: {len(self.scanned_files)}",
            f"Matched passages: {len(self.matches)}",
        ]
        for source in self.sources:
            lines.append(f"\n{source.source_path} - {source.similarity:.1%}")
            for match in source.matches:
                lines.append(f'- {match.word_count} words: "{match.query_passage}"')
        if self.skipped_sources:
            lines.append(f"\nSkipped files: {len(self.skipped_sources)}")
            lines.extend(f"- {item.path}: {item.reason}" for item in self.skipped_sources)
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class _Word:
    value: str
    start: int
    end: int


def compare_local_sources(
    text: str,
    selected_paths: str | os.PathLike[str] | Iterable[str | os.PathLike[str]],
    *,
    recursive: bool = True,
    min_match_words: int = 6,
    max_file_bytes: int = 2 * 1024 * 1024,
    max_source_words: int = 250_000,
    max_files: int = 500,
    max_matches_per_file: int = 10,
    max_passage_chars: int = 500,
) -> PlagiarismReport:
    """Compare text with explicitly selected local text files.

    The implementation performs no network access. Directories are limited to
    UTF-8 ``.txt`` and ``.md`` files. Symlinks, binary files, and files above
    the configured limits are skipped and explained in the report.
    """

    query = text or ""
    query_words = _words(query)
    if not query.strip() or not query_words:
        raise ValueError("Enter text to check.")
    if min_match_words < 1:
        raise ValueError("min_match_words must be at least 1.")
    if max_file_bytes < 1 or max_source_words < 1 or max_files < 1:
        raise ValueError("File limits must be positive.")
    if max_matches_per_file < 1 or max_passage_chars < 1:
        raise ValueError("Match limits must be positive.")

    paths = _coerce_paths(selected_paths)
    if not paths:
        raise ValueError("Select at least one local .txt or .md source.")

    files, skipped = _discover_files(paths, recursive=recursive, max_files=max_files)
    effective_minimum = min(min_match_words, len(query_words))
    query_values = [word.value for word in query_words]
    scanned: list[Path] = []
    comparisons: list[SourceComparison] = []
    all_query_intervals: list[tuple[int, int]] = []

    for path in files:
        source, reason = _read_local_text(path, max_file_bytes=max_file_bytes)
        if reason:
            skipped.append(SkippedSource(path, reason))
            continue
        source_words = _words(source)
        if not source_words:
            skipped.append(SkippedSource(path, "empty or no readable words"))
            continue
        if len(source_words) > max_source_words:
            skipped.append(SkippedSource(path, f"more than {max_source_words} words"))
            continue

        scanned.append(path)
        matcher = difflib.SequenceMatcher(
            None,
            query_values,
            [word.value for word in source_words],
            autojunk=True,
        )
        blocks = [block for block in matcher.get_matching_blocks() if block.size >= effective_minimum]
        if not blocks:
            continue

        intervals = [(block.a, block.a + block.size) for block in blocks]
        matched_words = _covered_words(intervals)
        all_query_intervals.extend(intervals)
        longest = sorted(blocks, key=lambda item: (-item.size, item.a, item.b))[:max_matches_per_file]
        matches = tuple(
            PassageMatch(
                source_path=path,
                query_passage=_passage(query, query_words, block.a, block.size, max_passage_chars),
                source_passage=_passage(source, source_words, block.b, block.size, max_passage_chars),
                similarity=1.0,
                word_count=block.size,
                query_word_start=block.a,
                source_word_start=block.b,
            )
            for block in longest
        )
        comparisons.append(
            SourceComparison(
                source_path=path,
                similarity=matched_words / len(query_words),
                matched_word_count=matched_words,
                matches=matches,
            )
        )

    comparisons.sort(key=lambda item: (-item.similarity, str(item.source_path).casefold()))
    skipped.sort(key=lambda item: str(item.path).casefold())
    similarity = _covered_words(all_query_intervals) / len(query_words)
    return PlagiarismReport(
        similarity=similarity,
        query_word_count=len(query_words),
        scanned_files=tuple(scanned),
        sources=tuple(comparisons),
        skipped_sources=tuple(skipped),
    )


def _coerce_paths(
    selected_paths: str | os.PathLike[str] | Iterable[str | os.PathLike[str]],
) -> list[Path]:
    if isinstance(selected_paths, (str, os.PathLike)):
        raw_paths = [selected_paths]
    else:
        raw_paths = list(selected_paths)
    return [Path(item).expanduser() for item in raw_paths if str(item).strip()]


def _discover_files(
    selected_paths: list[Path], *, recursive: bool, max_files: int
) -> tuple[list[Path], list[SkippedSource]]:
    discovered: list[Path] = []
    skipped: list[SkippedSource] = []
    seen: set[str] = set()

    for supplied in selected_paths:
        selected, reason = _validated_source_path(supplied)
        if reason:
            skipped.append(SkippedSource(supplied, reason))
            continue
        assert selected is not None
        if not selected.exists():
            skipped.append(SkippedSource(selected, "not found"))
            continue
        if selected.is_dir():
            candidates, child_skips = _walk_local_sources(selected, recursive=recursive)
            skipped.extend(child_skips)
        else:
            candidates = [selected]

        for candidate in candidates:
            if candidate.suffix.casefold() not in SUPPORTED_EXTENSIONS:
                if not selected.is_dir():
                    skipped.append(SkippedSource(candidate, "unsupported file type"))
                continue
            key = os.path.normcase(os.path.abspath(candidate))
            if key in seen:
                continue
            if len(discovered) >= max_files:
                skipped.append(SkippedSource(candidate, f"file limit of {max_files} reached"))
                continue
            seen.add(key)
            discovered.append(candidate)
    return discovered, skipped


def _walk_local_sources(
    root: Path,
    *,
    recursive: bool,
) -> tuple[list[Path], list[SkippedSource]]:
    files: list[Path] = []
    skipped: list[SkippedSource] = []
    try:
        children = sorted(root.iterdir(), key=lambda item: str(item).casefold())
    except OSError as exc:
        return [], [SkippedSource(root, f"cannot list directory: {exc}")]
    for supplied in children:
        child, reason = _validated_source_path(supplied)
        if reason:
            skipped.append(SkippedSource(supplied, reason))
            continue
        assert child is not None
        if child.is_dir():
            if recursive:
                nested, nested_skips = _walk_local_sources(child, recursive=True)
                files.extend(nested)
                skipped.extend(nested_skips)
        elif child.is_file():
            files.append(child)
    return files, skipped


def _validated_source_path(path: Path) -> tuple[Path | None, str]:
    try:
        return validate_local_data_file(
            path,
            purpose="Local comparison source",
        ), ""
    except UnsafePathError as exc:
        if "Network" in str(exc):
            return None, "network paths are not allowed"
        return None, "symbolic links, junctions, and unsafe paths are not followed"
    except (FileNotFoundError, OSError, RuntimeError):
        return None, "not found"


def _read_local_text(path: Path, *, max_file_bytes: int) -> tuple[str, str]:
    safe_path, reason = _validated_source_path(path)
    if reason or safe_path is None:
        return "", reason or "unsafe local path"
    path = safe_path
    try:
        size = path.stat().st_size
    except OSError as exc:
        return "", f"cannot inspect file: {exc}"
    if size > max_file_bytes:
        return "", f"larger than {max_file_bytes} bytes"
    if size == 0:
        return "", "empty file"
    try:
        data = path.read_bytes()
    except OSError as exc:
        return "", f"cannot read file: {exc}"
    if b"\x00" in data:
        return "", "binary file"
    try:
        return data.decode("utf-8-sig"), ""
    except UnicodeDecodeError:
        return "", "not valid UTF-8 text"


def _words(text: str) -> list[_Word]:
    return [
        _Word(match.group(0).casefold().replace("\u2019", "'"), match.start(), match.end())
        for match in WORD_PATTERN.finditer(text)
    ]


def _covered_words(intervals: list[tuple[int, int]]) -> int:
    if not intervals:
        return 0
    total = 0
    start, end = sorted(intervals)[0]
    for next_start, next_end in sorted(intervals)[1:]:
        if next_start <= end:
            end = max(end, next_end)
        else:
            total += end - start
            start, end = next_start, next_end
    return total + end - start


def _passage(text: str, words: list[_Word], start: int, count: int, limit: int) -> str:
    passage = text[words[start].start : words[start + count - 1].end].strip()
    if len(passage) <= limit:
        return passage
    return passage[: max(1, limit - 1)].rstrip() + "\u2026"
