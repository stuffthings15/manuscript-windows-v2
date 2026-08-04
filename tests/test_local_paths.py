from __future__ import annotations

import os

import pytest

import echo_link_core.local_paths as local_paths


def test_unc_paths_are_network_paths():
    assert local_paths.is_network_path(r"\\server\share\file.txt") is True
    assert local_paths.is_network_path("//server/share/file.txt") is True


@pytest.mark.skipif(os.name != "nt", reason="Windows mapped-drive detection")
def test_mapped_windows_drive_is_network_path(monkeypatch):
    monkeypatch.setattr(local_paths, "_windows_drive_type", lambda _root: 4)
    assert local_paths.is_network_path(r"Z:\folder\file.txt") is True

    monkeypatch.setattr(local_paths, "_windows_drive_type", lambda _root: 3)
    assert local_paths.is_network_path(r"C:\folder\file.txt") is False
