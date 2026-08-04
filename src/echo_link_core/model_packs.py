from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from .data_protection import (
    UnsafePathError,
    validate_local_data_file,
    validate_local_directory,
)
from .paths import model_root


DEFAULT_MAX_PACK_BYTES = 8 * 1024**3
HARD_MAX_PACK_BYTES = 16 * 1024**3
MAX_PACK_FILES = 4096
MAX_CONFIG_BYTES = 4 * 1024**2
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


class ModelPackError(ValueError):
    """Raised when a local speech-model pack or target is unsafe."""


class ModelPackExistsError(ModelPackError):
    """Raised when an import would replace an installed pack."""


class ModelPackNotFoundError(ModelPackError):
    """Raised when a requested installed pack is missing or invalid."""


@dataclass(frozen=True, slots=True)
class ModelPackInfo:
    name: str
    path: Path
    size_bytes: int
    file_count: int
    multilingual: bool


def validate_model_pack(
    folder: str | Path,
    *,
    max_bytes: int = DEFAULT_MAX_PACK_BYTES,
) -> ModelPackInfo:
    """Validate one faster-whisper model folder without changing it."""
    path = _absolute_path(folder, label="Model folder")
    path = _local_directory(path, label="Model folder")
    _reject_linked_components(path)
    _validate_pack_name(path.name)
    return _inspect_pack(path, pack_name=path.name, max_bytes=max_bytes)


def import_model_pack(
    source_folder: str | Path,
    *,
    user_model_root: str | Path,
    max_bytes: int = DEFAULT_MAX_PACK_BYTES,
) -> ModelPackInfo:
    """Copy a validated pack into a caller-supplied user model root."""
    source = validate_model_pack(source_folder, max_bytes=max_bytes)
    root = _safe_user_root(user_model_root, create=True)
    bundled_candidate = _direct_child(_safe_bundled_root(), source.name)
    if bundled_candidate.exists():
        raise ModelPackExistsError(
            f"A bundled model already uses this name: {source.name}"
        )
    destination = _direct_child(root, source.name)
    if destination.exists() or destination.is_symlink() or _is_junction(destination):
        raise ModelPackExistsError(f"Model pack already exists: {source.name}")

    staging = _direct_child(root, f"echo-link-import-{uuid.uuid4().hex}")
    staging.mkdir()
    try:
        shutil.copytree(source.path, staging, symlinks=True, dirs_exist_ok=True)
        copied = _inspect_pack(staging, pack_name=source.name, max_bytes=max_bytes)
        if copied.size_bytes != source.size_bytes or copied.file_count != source.file_count:
            raise ModelPackError("Model pack changed while it was being copied.")
        if destination.exists() or destination.is_symlink() or _is_junction(destination):
            raise ModelPackExistsError(f"Model pack already exists: {source.name}")
        staging.rename(destination)
    except Exception:
        if staging.exists() and staging.parent == root:
            shutil.rmtree(staging)
        raise
    return _inspect_pack(destination, pack_name=source.name, max_bytes=max_bytes)


def list_model_packs(
    *,
    user_model_root: str | Path,
    max_bytes: int = DEFAULT_MAX_PACK_BYTES,
) -> list[ModelPackInfo]:
    """List valid user-installed packs. Invalid folders are ignored."""
    root = _safe_user_root(user_model_root, create=False)
    if not root.exists():
        return []
    packs: list[ModelPackInfo] = []
    for candidate in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        try:
            _validate_pack_name(candidate.name)
            child = _direct_child(root, candidate.name)
            packs.append(_inspect_pack(child, pack_name=candidate.name, max_bytes=max_bytes))
        except (ModelPackError, OSError):
            continue
    return packs


def remove_model_pack(
    name: str,
    *,
    user_model_root: str | Path,
    max_bytes: int = DEFAULT_MAX_PACK_BYTES,
) -> ModelPackInfo:
    """Remove one validated direct child of the user model root."""
    _validate_pack_name(name)
    root = _safe_user_root(user_model_root, create=False)
    if not root.exists():
        raise ModelPackNotFoundError(f"Model pack is not installed: {name}")
    target = _direct_child(root, name)
    try:
        pack = _inspect_pack(target, pack_name=name, max_bytes=max_bytes)
    except (ModelPackError, OSError) as exc:
        raise ModelPackNotFoundError(f"Model pack is missing or unsafe: {name}") from exc
    shutil.rmtree(target)
    return pack


def _inspect_pack(path: Path, *, pack_name: str, max_bytes: int) -> ModelPackInfo:
    limit = _validated_limit(max_bytes)
    path = _local_directory(path, label="Model folder")
    if path.is_symlink() or _is_junction(path):
        raise ModelPackError("Model folder cannot be a link or junction.")

    total = 0
    count = 0
    for entry in _walk_pack_entries(path):
        if entry.is_symlink() or _is_junction(entry):
            raise ModelPackError("Model packs cannot contain links or junctions.")
        if entry.is_dir():
            continue
        if not entry.is_file():
            raise ModelPackError("Model packs can contain only regular files and folders.")
        count += 1
        if count > MAX_PACK_FILES:
            raise ModelPackError(f"Model pack contains more than {MAX_PACK_FILES} files.")
        total += entry.stat().st_size
        if total > limit:
            raise ModelPackError(f"Model pack is larger than {limit} bytes.")

    config_path = path / "config.json"
    model_path = path / "model.bin"
    if (
        not config_path.is_file()
        or config_path.is_symlink()
        or _is_junction(config_path)
        or not model_path.is_file()
        or model_path.is_symlink()
        or _is_junction(model_path)
    ):
        raise ModelPackError("Model pack requires regular config.json and model.bin files.")
    if model_path.stat().st_size <= 0:
        raise ModelPackError("model.bin cannot be empty.")
    if config_path.stat().st_size > MAX_CONFIG_BYTES:
        raise ModelPackError(f"config.json is larger than {MAX_CONFIG_BYTES} bytes.")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ModelPackError("config.json must contain valid UTF-8 JSON.") from exc
    if not isinstance(config, dict):
        raise ModelPackError("config.json must contain a JSON object.")

    return ModelPackInfo(
        name=pack_name,
        path=path,
        size_bytes=total,
        file_count=count,
        multilingual=not pack_name.casefold().endswith(".en"),
    )


def _safe_user_root(root_value: str | Path, *, create: bool) -> Path:
    root = _absolute_path(root_value, label="User model root")
    try:
        root = validate_local_data_file(
            root,
            purpose="User model root",
            create_parent=create,
        )
        if root.exists():
            root = validate_local_directory(
                root,
                purpose="User model root",
                create=False,
            )
        elif create:
            root = validate_local_directory(
                root,
                purpose="User model root",
                create=True,
            )
    except (UnsafePathError, FileNotFoundError, OSError) as exc:
        raise ModelPackError(str(exc)) from exc
    _reject_linked_components(root)

    bundled = _safe_bundled_root()
    resolved = root.resolve(strict=False)
    if _overlaps(resolved, bundled):
        raise ModelPackError("User model root cannot overlap bundled models.")
    if root.exists() and not root.is_dir():
        raise ModelPackError("User model root is not a directory.")
    return root.resolve(strict=False)


def _safe_bundled_root() -> Path:
    root = _absolute_path(model_root(), label="Bundled model root")
    try:
        candidate = validate_local_data_file(root, purpose="Bundled model root")
        if candidate.exists():
            return validate_local_directory(
                candidate,
                purpose="Bundled model root",
                create=False,
            )
        return candidate
    except (UnsafePathError, FileNotFoundError, OSError) as exc:
        raise ModelPackError(str(exc)) from exc


def _local_directory(path: Path, *, label: str) -> Path:
    try:
        return validate_local_directory(
            path,
            purpose=label,
            create=False,
        )
    except (UnsafePathError, FileNotFoundError, OSError) as exc:
        raise ModelPackError(str(exc)) from exc


def _walk_pack_entries(root: Path):
    for candidate in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        try:
            entry = validate_local_data_file(
                candidate,
                purpose="Model pack entry",
            )
        except (UnsafePathError, FileNotFoundError, OSError) as exc:
            raise ModelPackError(str(exc)) from exc
        if entry.is_symlink() or _is_junction(entry):
            raise ModelPackError("Model packs cannot contain links or junctions.")
        yield entry
        if entry.is_dir():
            yield from _walk_pack_entries(entry)


def _absolute_path(value: str | Path, *, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ModelPackError(f"{label} must be an absolute path.")
    if ".." in path.parts:
        raise ModelPackError(f"{label} cannot contain parent traversal.")
    return path


def _direct_child(root: Path, name: str) -> Path:
    candidate = root / name
    resolved = candidate.resolve(strict=False)
    if resolved.parent != root.resolve(strict=False):
        raise ModelPackError("Model pack target must be a direct child of the user model root.")
    return candidate


def _validate_pack_name(name: str) -> None:
    if not _SAFE_NAME.fullmatch(name) or name.endswith("."):
        raise ModelPackError(
            "Model name must be 1-64 letters, numbers, dots, dashes, or underscores."
        )
    if name.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        raise ModelPackError("Model name is reserved by Windows.")


def _validated_limit(max_bytes: int) -> int:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
        raise ModelPackError("Model pack size limit must be an integer.")
    if max_bytes <= 0 or max_bytes > HARD_MAX_PACK_BYTES:
        raise ModelPackError(f"Model pack size limit must be 1-{HARD_MAX_PACK_BYTES} bytes.")
    return max_bytes


def _reject_linked_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.exists() and (current.is_symlink() or _is_junction(current)):
            raise ModelPackError("Model paths cannot use links or junctions.")


def _is_junction(path: Path) -> bool:
    checker = getattr(path, "is_junction", None)
    return bool(checker and checker())


def _overlaps(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents
