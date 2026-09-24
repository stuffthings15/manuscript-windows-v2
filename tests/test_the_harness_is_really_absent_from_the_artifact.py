"""Read the built archive, not the spec that was supposed to produce it.

tests/test_the_harness_is_not_shipped.py parses EchoLink.spec with ast and
asserts the conditional is written correctly. It was green on 2026-09-24 while
the first production archive ever built contained
tools.windows_workflow_probe anyway - keystroke synthesis, microphone, a
spawned process - because the conditional only ADDED the module for a testing
build and src/echo_link_app/main.py imports it directly, which PyInstaller
follows into the archive. Asserting the shape of the build instead of its
output is precisely the failure CLAUDE.md warns about, and this is the second
time it has bitten.

So this one opens the artifact. It skips when there is nothing built, which
means it cannot replace the spec test - it can only catch what that one
structurally cannot see. Run a build first and it has teeth.
"""

from __future__ import annotations

import json
import pathlib

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "dist" / "ManuscriptV2" / "ManuscriptV2.exe"
CHANNEL = ROOT / "dist" / "ManuscriptV2" / "_internal" / "resources" / "build_info.json"


def _archive_modules() -> list[str]:
    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

    reader = CArchiveReader(str(BUNDLE))
    pyz = pathlib.Path(__file__).parent / ".pyz-under-test"
    pyz.write_bytes(reader.extract("PYZ.pyz"))
    try:
        return list(ZlibArchiveReader(str(pyz)).toc)
    finally:
        pyz.unlink(missing_ok=True)


def _built_channel() -> str:
    return json.loads(CHANNEL.read_text(encoding="utf-8")).get("build_channel", "")


@pytest.mark.skipif(
    not BUNDLE.is_file() or not CHANNEL.is_file(),
    reason="No build in dist/ManuscriptV2 to inspect. Build first, then this bites.",
)
def test_a_production_archive_does_not_carry_the_probe():
    if _built_channel() != "production":
        pytest.skip(f"dist holds a {_built_channel()!r} build, not a production one")

    carried = [m for m in _archive_modules() if "windows_workflow_probe" in m]
    assert carried == [], (
        "A production archive contains the keystroke probe: "
        f"{carried}. excludes= in EchoLink.spec is what removes it; "
        "leaving it out of hiddenimports is not enough, because main.py "
        "imports it and PyInstaller follows that."
    )


@pytest.mark.skipif(
    not BUNDLE.is_file() or not CHANNEL.is_file(),
    reason="No build in dist/ManuscriptV2 to inspect.",
)
def test_a_testing_archive_still_carries_it_because_acceptance_needs_it():
    """The exclusion must not overshoot.

    tools/installer_lifecycle_probe.py drives --workflow-probe against the
    INSTALLED executable; that is how release acceptance checks the packaged
    hotkey, HUD, transcription and insertion path end to end. A testing build
    with no harness would break that silently.
    """
    if _built_channel() != "testing-unlocked":
        pytest.skip(f"dist holds a {_built_channel()!r} build, not a testing one")

    carried = [m for m in _archive_modules() if "windows_workflow_probe" in m]
    assert carried, "A testing build must keep the harness for release acceptance"
