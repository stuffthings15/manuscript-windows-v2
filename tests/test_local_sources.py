from pathlib import Path

from echo_link_core.local_sources import LocalSourceIndex


def test_search_returns_exact_local_path_line_and_snippet(tmp_path: Path):
    source = tmp_path / "approved.md"
    source.write_text(
        "Project notes\n"
        "Echo-Link keeps speech processing on this computer.\n"
        "No cloud upload is required.\n",
        encoding="utf-8",
    )
    index = LocalSourceIndex()

    assert index.search("speech processing") == []
    result = index.approve([source])
    matches = index.search("speech processing")

    assert result[0].indexed is True
    assert index.approved_paths == (str(source.resolve()),)
    assert len(matches) == 1
    assert matches[0].path == str(source.resolve())
    assert matches[0].line_number == 2
    assert matches[0].citation == f"{source.resolve()}:2"
    assert "Echo-Link keeps speech processing" in matches[0].snippet


def test_approval_is_explicit_and_only_allows_text_documents(tmp_path: Path):
    allowed = tmp_path / "notes.txt"
    allowed.write_text("approved fact", encoding="utf-8")
    unsupported = tmp_path / "notes.docx"
    unsupported.write_bytes(b"approved fact")
    directory = tmp_path / "folder.md"
    directory.mkdir()

    index = LocalSourceIndex()
    results = index.approve([allowed, unsupported, directory, tmp_path / "missing.txt"])

    assert [item.indexed for item in results] == [True, False, False, False]
    assert "Only .txt and .md" in results[1].reason
    assert "not a file" in results[2].reason
    assert "not found" in results[3].reason
    assert index.search("approved fact")[0].path == str(allowed.resolve())


def test_empty_binary_and_large_files_are_rejected_without_partial_index(tmp_path: Path):
    empty = tmp_path / "empty.txt"
    empty.write_text(" \n\t", encoding="utf-8")
    binary = tmp_path / "binary.md"
    binary.write_bytes(b"text\x00hidden")
    large = tmp_path / "large.txt"
    large.write_bytes(b"searchable" * 20)

    index = LocalSourceIndex(max_file_bytes=50)
    results = index.approve([empty, binary, large])

    assert [item.indexed for item in results] == [False, False, False]
    assert [item.reason for item in results] == [
        "File is empty.",
        "File is binary or is not supported text.",
        "File exceeds the size limit.",
    ]
    assert index.approved_paths == ()
    assert index.search("searchable") == []


def test_search_ranks_exact_phrase_and_limits_results(tmp_path: Path):
    first = tmp_path / "first.md"
    first.write_text(
        "Local data is useful. Search works here.\n"
        "Local data search gives the exact phrase.\n"
        "Another local data search line.\n",
        encoding="utf-8",
    )
    second = tmp_path / "second.txt"
    second.write_text("Search local data in reverse order.\n", encoding="utf-8")
    index = LocalSourceIndex()
    index.approve([first, second])

    matches = index.search("local data search", limit=2)

    assert len(matches) == 2
    assert matches[0].path == str(first.resolve())
    assert matches[0].line_number == 2
    assert matches[0].score >= matches[1].score
    assert index.search("") == []
    assert index.search("local", limit=0) == []


def test_index_is_memory_only_and_can_revoke_or_clear(tmp_path: Path):
    source = tmp_path / "memory.md"
    source.write_text("Transient source detail.", encoding="utf-8")
    index = LocalSourceIndex()
    index.approve([source])
    source.unlink()

    assert index.search("transient source")[0].line_number == 1
    assert index.revoke(source) is True
    assert index.search("transient source") == []

    second = tmp_path / "second.txt"
    second.write_text("Another detail.", encoding="utf-8")
    index.approve([second])
    index.clear()
    assert index.approved_paths == ()
    assert index.search("another detail") == []


def test_utf16_bom_text_and_long_snippet_are_safe(tmp_path: Path):
    source = tmp_path / "unicode.txt"
    source.write_text("Heading\n" + ("word " * 100) + "needle\nEnd", encoding="utf-16")
    index = LocalSourceIndex(max_snippet_chars=60)

    assert index.approve([source])[0].indexed is True
    match = index.search("needle")[0]

    assert match.line_number == 2
    assert len(match.snippet) <= 60
    assert match.snippet.endswith("...")


def test_search_matches_whole_terms_not_substrings(tmp_path: Path):
    source = tmp_path / "terms.txt"
    source.write_text("The theme is here.\nA distinct he token is here.\n", encoding="utf-8")
    index = LocalSourceIndex()
    index.approve([source])

    matches = index.search("he")

    assert len(matches) == 1
    assert matches[0].line_number == 2


def test_symbolic_link_is_not_approved(tmp_path: Path):
    source = tmp_path / "source.txt"
    source.write_text("private local source", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(source)
    except OSError:
        return
    result = LocalSourceIndex().approve([link])[0]
    assert result.indexed is False
    assert "Symbolic" in result.reason


def test_network_source_is_rejected_before_file_access():
    result = LocalSourceIndex().approve([Path(r"\\server\share\source.txt")])[0]
    assert result.indexed is False
    assert result.reason == "Network paths are not allowed."
