from __future__ import annotations

from pathlib import Path

import pytest

from echo_link_app import sound_cues
from echo_link_core import data_protection as protection
from echo_link_core.data_protection import UnsafePathError
from echo_link_core.local_sources import LocalSourceIndex
from echo_link_core.model_packs import ModelPackError, import_model_pack, validate_model_pack
from echo_link_core.plagiarism import compare_local_sources
from tools import windows_target_insertion_probe, windows_workflow_probe


def _make_pack(parent: Path, name: str = "small.en") -> Path:
    pack = parent / name
    pack.mkdir(parents=True)
    (pack / "config.json").write_text("{}", encoding="utf-8")
    (pack / "model.bin").write_bytes(b"local-model")
    return pack


def _mark_as_mapped(monkeypatch, target: Path) -> None:
    original = protection.is_network_path
    expected = target.absolute()
    monkeypatch.setattr(
        protection,
        "is_network_path",
        lambda value: Path(value).absolute() == expected or original(value),
    )


def _mark_as_link(monkeypatch, target: Path) -> None:
    original = protection._is_link
    expected = target.absolute()
    monkeypatch.setattr(
        protection,
        "_is_link",
        lambda value: Path(value).absolute() == expected or original(value),
    )


def test_custom_wav_rejects_unc_mapped_drive_and_linked_parent(tmp_path, monkeypatch):
    assert sound_cues.resolve_cue_path(
        r"\\server\share\private.wav",
        "Speech On.wav",
    ) is None

    mapped = tmp_path / "mapped.wav"
    mapped.write_bytes(b"RIFF")
    _mark_as_mapped(monkeypatch, mapped)
    assert sound_cues.resolve_cue_path(str(mapped), "Speech On.wav") is None

    monkeypatch.undo()
    linked_parent = tmp_path / "linked-cues"
    linked_parent.mkdir()
    cue = linked_parent / "private.wav"
    cue.write_bytes(b"RIFF")
    _mark_as_link(monkeypatch, linked_parent)
    assert sound_cues.resolve_cue_path(str(cue), "Speech On.wav") is None


def test_model_pack_sources_and_destinations_reject_remote_and_linked_paths(
    tmp_path,
    monkeypatch,
):
    with pytest.raises(ModelPackError, match="Network"):
        validate_model_pack(Path(r"\\server\share\model.en"))

    source = _make_pack(tmp_path / "source")
    _mark_as_mapped(monkeypatch, source)
    with pytest.raises(ModelPackError, match="Network"):
        validate_model_pack(source)

    monkeypatch.undo()
    linked_parent = tmp_path / "linked-source"
    linked_parent.mkdir()
    linked_pack = _make_pack(linked_parent)
    _mark_as_link(monkeypatch, linked_parent)
    with pytest.raises(ModelPackError, match="Symbolic-link"):
        validate_model_pack(linked_pack)

    monkeypatch.undo()
    destination = tmp_path / "mapped-model-root"
    _mark_as_mapped(monkeypatch, destination)
    with pytest.raises(ModelPackError, match="Network"):
        import_model_pack(source, user_model_root=destination)
    assert not destination.exists()


def test_approved_sources_and_plagiarism_reject_mapped_and_linked_paths(
    tmp_path,
    monkeypatch,
):
    mapped = tmp_path / "mapped.txt"
    mapped.write_text("one two three four five six", encoding="utf-8")
    _mark_as_mapped(monkeypatch, mapped)

    approval = LocalSourceIndex().approve([mapped])[0]
    report = compare_local_sources("one two three four five six", mapped)
    assert approval.indexed is False
    assert approval.reason == "Network paths are not allowed."
    assert report.scanned_files == ()
    assert report.skipped_sources[0].reason == "network paths are not allowed"

    monkeypatch.undo()
    linked_parent = tmp_path / "linked-sources"
    linked_parent.mkdir()
    linked = linked_parent / "source.md"
    linked.write_text("one two three four five six", encoding="utf-8")
    _mark_as_link(monkeypatch, linked_parent)

    approval = LocalSourceIndex().approve([linked])[0]
    report = compare_local_sources("one two three four five six", linked)
    assert approval.indexed is False
    assert "junctions" in approval.reason
    assert report.scanned_files == ()
    assert "junctions" in report.skipped_sources[0].reason


@pytest.mark.parametrize(
    "writer",
    [windows_workflow_probe._write_json_atomic, windows_target_insertion_probe._write_json],
)
def test_private_probe_evidence_rejects_remote_and_linked_destinations(
    writer,
    tmp_path,
    monkeypatch,
):
    with pytest.raises(UnsafePathError, match="Network"):
        writer(Path(r"\\server\share\private-evidence.json"), {"private": "text"})

    mapped = tmp_path / "mapped-evidence.json"
    _mark_as_mapped(monkeypatch, mapped)
    with pytest.raises(UnsafePathError, match="Network"):
        writer(mapped, {"private": "text"})
    assert not mapped.exists()

    monkeypatch.undo()
    linked_parent = tmp_path / "linked-evidence"
    linked_parent.mkdir()
    _mark_as_link(monkeypatch, linked_parent)
    with pytest.raises(UnsafePathError, match="Symbolic-link"):
        writer(linked_parent / "private.json", {"private": "text"})
    assert list(linked_parent.iterdir()) == []
