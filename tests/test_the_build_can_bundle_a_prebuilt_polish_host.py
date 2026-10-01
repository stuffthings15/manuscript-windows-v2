"""The release build can bundle a polish host that was already built.

Found 2026-10-01. Every installer build recompiled the host, and .NET builds
are not byte-identical, so every installer carried a new unsigned hash.
Smart App Control judges unsigned files by per-file cloud reputation: on
this machine it allowed the host compiled at 08:51 and blocked the identical
source compiled inside the installer build at 09:55 ("An Application Control
policy has blocked this file"). An installed app whose host is blocked falls
back from the bundled runtime with nothing visibly wrong.

--polish-host PATH stages a known executable - the one Windows already runs,
or later a signed one - instead of compiling a fresh one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import build as build_tool


def _fake_host(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    # The build refuses anything that could not be a self-contained host.
    path.write_bytes(b"MZ" + b"\0" * (2 * 1024 * 1024))
    return path


def test_the_parser_accepts_a_prebuilt_polish_host() -> None:
    args = build_tool.parser().parse_args(["--polish-host", "C:/hosts/host.exe"])
    assert args.polish_host == "C:/hosts/host.exe"


def test_a_prebuilt_host_is_staged_and_nothing_is_compiled(tmp_path, monkeypatch):
    monkeypatch.setattr(build_tool, "ROOT", tmp_path)
    compiled = []
    monkeypatch.setattr(
        build_tool.subprocess, "run", lambda *a, **k: compiled.append(a)
    )
    prebuilt = _fake_host(tmp_path / "elsewhere" / "Manuscript.PolishHost.exe")

    staged = build_tool._stage_polish_host(str(prebuilt))

    assert compiled == []
    assert staged == (
        tmp_path / "build" / "generated" / "polish-host" / "Manuscript.PolishHost.exe"
    )
    assert staged.read_bytes() == prebuilt.read_bytes()


def test_without_a_prebuilt_host_the_host_is_compiled(tmp_path, monkeypatch):
    monkeypatch.setattr(build_tool, "ROOT", tmp_path)
    compiled = []
    monkeypatch.setattr(
        build_tool.subprocess, "run", lambda *a, **k: compiled.append(a[0])
    )

    build_tool._stage_polish_host("")

    assert len(compiled) == 1
    assert compiled[0][-1].endswith("build_polish_host.py")


@pytest.mark.parametrize("contents", [None, b"MZ tiny"])
def test_a_missing_or_implausible_prebuilt_host_is_refused(
    tmp_path, monkeypatch, contents
):
    monkeypatch.setattr(build_tool, "ROOT", tmp_path)
    monkeypatch.setattr(build_tool.subprocess, "run", lambda *a, **k: None)
    candidate = tmp_path / "host.exe"
    if contents is not None:
        candidate.write_bytes(contents)

    with pytest.raises(SystemExit):
        build_tool._stage_polish_host(str(candidate))
