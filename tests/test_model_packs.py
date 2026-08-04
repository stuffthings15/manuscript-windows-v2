from __future__ import annotations

from pathlib import Path

import pytest

from echo_link_core.model_packs import (
    HARD_MAX_PACK_BYTES,
    ModelPackError,
    ModelPackExistsError,
    ModelPackNotFoundError,
    import_model_pack,
    list_model_packs,
    remove_model_pack,
    validate_model_pack,
)


def _make_pack(parent: Path, name: str = "small.en") -> Path:
    pack = parent / name
    pack.mkdir(parents=True)
    (pack / "config.json").write_text('{"model_type": "whisper"}', encoding="utf-8")
    (pack / "model.bin").write_bytes(b"local-model")
    (pack / "tokenizer.json").write_text("{}", encoding="utf-8")
    return pack


def test_validate_requires_safe_name_core_files_valid_json_and_size(tmp_path):
    parent = tmp_path / "sources"
    valid = _make_pack(parent)
    info = validate_model_pack(valid)
    assert info.name == "small.en"
    assert info.file_count == 3
    assert info.size_bytes > 0
    assert info.multilingual is False

    (valid / "config.json").write_text("bad json", encoding="utf-8")
    with pytest.raises(ModelPackError, match="valid UTF-8 JSON"):
        validate_model_pack(valid)
    (valid / "config.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ModelPackError, match="larger"):
        validate_model_pack(valid, max_bytes=2)

    unsafe = _make_pack(parent, "..unsafe")
    with pytest.raises(ModelPackError, match="Model name"):
        validate_model_pack(unsafe)

    missing = _make_pack(parent, "missing.en")
    (missing / "model.bin").unlink()
    with pytest.raises(ModelPackError, match="requires regular"):
        validate_model_pack(missing)


def test_import_copies_pack_and_does_not_replace_existing(tmp_path):
    source = _make_pack(tmp_path / "source")
    user_root = tmp_path / "user-models"
    imported = import_model_pack(source, user_model_root=user_root)
    assert imported.path == user_root / "small.en"
    assert imported.path != source
    assert (imported.path / "model.bin").read_bytes() == b"local-model"

    (source / "model.bin").write_bytes(b"changed")
    assert (imported.path / "model.bin").read_bytes() == b"local-model"
    with pytest.raises(ModelPackExistsError):
        import_model_pack(source, user_model_root=user_root)


def test_list_returns_only_valid_direct_user_packs(tmp_path):
    root = tmp_path / "user-models"
    _make_pack(root, "base.en")
    _make_pack(root, "small")
    invalid = root / "notes"
    invalid.mkdir()
    (invalid / "readme.txt").write_text("not a model", encoding="utf-8")

    packs = list_model_packs(user_model_root=root)
    assert [pack.name for pack in packs] == ["base.en", "small"]
    assert [pack.multilingual for pack in packs] == [False, True]


def test_remove_deletes_only_a_valid_direct_child(tmp_path):
    root = tmp_path / "user-models"
    target = _make_pack(root, "tiny.en")
    unrelated = root / "keep-me"
    unrelated.mkdir()
    (unrelated / "notes.txt").write_text("keep", encoding="utf-8")

    removed = remove_model_pack("tiny.en", user_model_root=root)
    assert removed.name == "tiny.en"
    assert not target.exists()
    assert unrelated.exists()

    with pytest.raises(ModelPackNotFoundError):
        remove_model_pack("keep-me", user_model_root=root)
    assert unrelated.exists()
    with pytest.raises(ModelPackError):
        remove_model_pack("../keep-me", user_model_root=root)


def test_rejects_linked_source_content_and_target(tmp_path, monkeypatch):
    source = _make_pack(tmp_path / "source")
    link = source / "linked.txt"
    link.write_text("outside", encoding="utf-8")
    monkeypatch.setattr(
        "echo_link_core.model_packs._is_junction",
        lambda path: path.name in {"linked.txt", "linked-user-models"},
    )
    with pytest.raises(ModelPackError, match="links or junctions"):
        validate_model_pack(source)

    clean = _make_pack(tmp_path / "clean", "base.en")
    real_root = tmp_path / "real-user-models"
    linked_root = tmp_path / "linked-user-models"
    real_root.mkdir()
    linked_root.mkdir()
    with pytest.raises(ModelPackError, match="links or junctions"):
        import_model_pack(clean, user_model_root=linked_root)
    assert not (real_root / "base.en").exists()


def test_rejects_relative_root_bundled_overlap_and_unbounded_limit(tmp_path, monkeypatch):
    source = _make_pack(tmp_path / "source")
    with pytest.raises(ModelPackError, match="absolute"):
        import_model_pack(source, user_model_root=Path("relative-models"))
    traversing_root = tmp_path / "unused" / ".." / "user-models"
    with pytest.raises(ModelPackError, match="parent traversal"):
        import_model_pack(source, user_model_root=traversing_root)

    bundled = tmp_path / "bundled"
    bundled.mkdir()
    monkeypatch.setattr("echo_link_core.model_packs.model_root", lambda: bundled)
    with pytest.raises(ModelPackError, match="bundled"):
        import_model_pack(source, user_model_root=bundled)
    with pytest.raises(ModelPackError, match="bundled"):
        list_model_packs(user_model_root=tmp_path)
    with pytest.raises(ModelPackError, match="size limit"):
        validate_model_pack(source, max_bytes=HARD_MAX_PACK_BYTES + 1)


def test_import_rejects_name_used_by_bundled_model(tmp_path, monkeypatch):
    source = _make_pack(tmp_path / "source", "base.en")
    bundled = tmp_path / "bundled"
    _make_pack(bundled, "base.en")
    monkeypatch.setattr("echo_link_core.model_packs.model_root", lambda: bundled)

    with pytest.raises(ModelPackExistsError, match="bundled model"):
        import_model_pack(source, user_model_root=tmp_path / "user-models")
