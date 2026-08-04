from __future__ import annotations

import json

import echo_link_core.version as version
from echo_link_core.version import build_commit


def test_source_build_commit_is_available():
    value = build_commit()
    assert value != "unknown"
    assert len(value) >= 7


def test_build_commit_prefers_valid_build_info(monkeypatch, tmp_path):
    commit = "1234567890abcdef"
    build_info = tmp_path / "resources" / "build_info.json"
    build_info.parent.mkdir(parents=True)
    build_info.write_text(json.dumps({"commit": commit}), encoding="utf-8")
    monkeypatch.delenv("ECHOLINK_BUILD_COMMIT", raising=False)
    monkeypatch.setattr(version, "resource_path", lambda *_parts: build_info)
    monkeypatch.setattr(version, "project_root", lambda: tmp_path / "not-a-repository")

    assert build_commit() == commit


def test_build_commit_finds_parent_repository_when_nested(monkeypatch, tmp_path):
    commit = "abcdef1234567890abcdef1234567890abcdef12"
    repository = tmp_path / "repository"
    nested = repository / "Manuscript" / "Windows"
    reference = repository / ".git" / "refs" / "heads" / "main"
    reference.parent.mkdir(parents=True)
    reference.write_text(commit + "\n", encoding="utf-8")
    (repository / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    build_info = nested / "resources" / "build_info.json"
    build_info.parent.mkdir(parents=True)
    build_info.write_text('{"commit": "unknown"}\n', encoding="utf-8")
    monkeypatch.delenv("ECHOLINK_BUILD_COMMIT", raising=False)
    monkeypatch.setattr(version, "resource_path", lambda *_parts: build_info)
    monkeypatch.setattr(version, "project_root", lambda: nested)

    assert build_commit() == commit[:12]
