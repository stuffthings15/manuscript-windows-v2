from __future__ import annotations

import os
import sys
from pathlib import Path

from .data_protection import validate_local_directory


APP_NAME = "ManuscriptWindowsV2"


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resource_root() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root)
    return project_root()


def resource_path(*parts: str) -> Path:
    return resource_root().joinpath(*parts)


def data_root() -> Path:
    override = os.environ.get("MANUSCRIPT_V2_DATA_DIR") or os.environ.get(
        "ECHOLINK_DATA_DIR"
    )
    if override:
        root = Path(override).expanduser()
    else:
        local = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        root = Path(local or Path.home()) / APP_NAME
    return validate_local_directory(
        root,
        purpose="Manuscript data folder",
        create=True,
    )


def model_root() -> Path:
    override = os.environ.get("MANUSCRIPT_V2_MODEL_DIR") or os.environ.get(
        "ECHOLINK_MODEL_DIR"
    )
    if override:
        return validate_local_directory(
            Path(override).expanduser(),
            purpose="Manuscript model folder",
            create=False,
        )
    return resource_path("models")


def user_model_root() -> Path:
    return validate_local_directory(
        data_root() / "models",
        purpose="Manuscript user model folder",
        create=True,
    )


def log_path() -> Path:
    return data_root() / "echo-link.log"
