from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .paths import project_root, resource_path


_COMMIT_PATTERN = re.compile(r"[0-9a-fA-F]{7,64}")


def _valid_commit(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate if _COMMIT_PATTERN.fullmatch(candidate) else None


def _commit_from_build_info(path: Path) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    return _valid_commit(payload.get("commit"))


def _git_directory(root: Path) -> Path | None:
    marker = root / ".git"
    if marker.is_dir():
        return marker
    if not marker.is_file():
        return None
    try:
        value = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not value.casefold().startswith("gitdir:"):
        return None
    path = Path(value.split(":", 1)[1].strip())
    return path if path.is_absolute() else (root / path).resolve()


def _commit_from_git(root: Path) -> str | None:
    git = _git_directory(root)
    if git is None:
        return None
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    direct = _valid_commit(head)
    if direct:
        return direct[:12]
    if not head.startswith("ref: "):
        return None
    reference = head[5:].strip()
    try:
        loose = (git / reference).read_text(encoding="utf-8").strip()
    except OSError:
        loose = ""
    commit = _valid_commit(loose)
    if commit:
        return commit[:12]
    try:
        packed_refs = (git / "packed-refs").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in packed_refs:
        if not line or line.startswith(("#", "^")):
            continue
        value, separator, name = line.partition(" ")
        if separator and name == reference:
            commit = _valid_commit(value)
            return commit[:12] if commit else None
    return None


def build_commit() -> str:
    configured = (os.environ.get("ECHOLINK_BUILD_COMMIT") or "").strip()
    if configured:
        return configured
    build_info = resource_path("resources", "build_info.json")
    commit = _commit_from_build_info(build_info)
    if commit:
        return commit
    root = project_root()
    for candidate in (root, *root.parents):
        commit = _commit_from_git(candidate)
        if commit:
            return commit
    return "unknown"
