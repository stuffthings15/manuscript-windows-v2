from __future__ import annotations

from pathlib import Path

import pytest

from echo_link_core.plagiarism import compare_local_sources


def test_compares_selected_local_directory_and_reports_passages(tmp_path: Path):
    source = tmp_path / "sources"
    source.mkdir()
    essay = source / "essay.txt"
    essay.write_text(
        "An unrelated opening. The QUICK brown fox jumps over the lazy dog! Closing words.",
        encoding="utf-8",
    )
    notes = source / "notes.md"
    notes.write_text("Nothing from the submitted text appears in these notes.", encoding="utf-8")

    report = compare_local_sources(
        "My opening says the quick brown fox jumps over the lazy dog. My ending is original.",
        source,
    )

    assert report.query_word_count == 16
    assert report.scanned_files == (essay.resolve(), notes.resolve())
    assert len(report.sources) == 1
    assert report.sources[0].source_path == essay.resolve()
    assert report.sources[0].matched_word_count == 9
    assert report.sources[0].similarity == pytest.approx(9 / 16)
    assert report.similarity == pytest.approx(9 / 16)
    match = report.matches[0]
    assert match.word_count == 9
    assert match.similarity == 1.0
    assert match.query_passage == "the quick brown fox jumps over the lazy dog"
    assert match.source_passage == "The QUICK brown fox jumps over the lazy dog"
    assert str(essay.resolve()) in report.as_text()


def test_combines_query_coverage_across_explicit_files(tmp_path: Path):
    first = tmp_path / "first.txt"
    first.write_text("alpha bravo charlie delta echo foxtrot", encoding="utf-8")
    second = tmp_path / "second.md"
    second.write_text("golf hotel india juliet kilo lima", encoding="utf-8")

    report = compare_local_sources(
        "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima",
        [first, second, first],
    )

    assert len(report.scanned_files) == 2
    assert len(report.sources) == 2
    assert report.similarity == 1.0
    assert all(source.similarity == 0.5 for source in report.sources)


def test_safely_skips_empty_binary_large_missing_and_unsupported_files(tmp_path: Path):
    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"")
    binary = tmp_path / "binary.txt"
    binary.write_bytes(b"words\x00more words")
    large = tmp_path / "large.md"
    large.write_text("x" * 100, encoding="utf-8")
    unsupported = tmp_path / "source.pdf"
    unsupported.write_bytes(b"not a real PDF")
    missing = tmp_path / "missing.txt"

    report = compare_local_sources(
        "one two three four five six",
        [empty, binary, large, unsupported, missing],
        max_file_bytes=32,
    )

    assert report.scanned_files == ()
    assert report.matches == ()
    assert report.similarity == 0.0
    reasons = {item.path.name: item.reason for item in report.skipped_sources}
    assert reasons["empty.txt"] == "empty file"
    assert reasons["binary.txt"] == "binary file"
    assert "larger than" in reasons["large.md"]
    assert reasons["source.pdf"] == "unsupported file type"
    assert reasons["missing.txt"] == "not found"


def test_non_recursive_scan_ignores_nested_files(tmp_path: Path):
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "match.txt").write_text("one two three four five six", encoding="utf-8")

    report = compare_local_sources(
        "one two three four five six",
        tmp_path,
        recursive=False,
    )

    assert report.scanned_files == ()
    assert report.similarity == 0.0


@pytest.mark.parametrize("text", ["", "   ", "---"])
def test_rejects_text_without_words(tmp_path: Path, text: str):
    source = tmp_path / "source.txt"
    source.write_text("some local source text", encoding="utf-8")

    with pytest.raises(ValueError, match="Enter text"):
        compare_local_sources(text, source)


def test_requires_a_selected_source():
    with pytest.raises(ValueError, match="Select at least one"):
        compare_local_sources("one two three four five six", [])


def test_limits_source_word_count_before_comparison(tmp_path: Path):
    source = tmp_path / "many.txt"
    source.write_text("one two three four five six seven", encoding="utf-8")

    report = compare_local_sources(
        "one two three four five six",
        source,
        max_source_words=5,
    )

    assert report.scanned_files == ()
    assert report.skipped_sources[0].reason == "more than 5 words"


def test_refuses_network_paths_without_accessing_them():
    network_path = Path(r"\\server\share\source.txt")

    report = compare_local_sources("one two three four five six", network_path)

    assert report.scanned_files == ()
    assert len(report.skipped_sources) == 1
    assert report.skipped_sources[0].path == network_path
    assert report.skipped_sources[0].reason == "network paths are not allowed"
