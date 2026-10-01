"""The product version was declared four times and agreed nowhere.

    pyproject.toml                          0.1.0
    packaging/EchoLink.iss                  0.1.0
    packaging/manuscript_version_info.txt   0.1.0
    native/.../Manuscript.PolishHost.csproj 2.0.0

Nothing checked that any of them matched, and none of them reached the running
app: identity at runtime was `build_commit()`, a git hash, with no tags in the
repository. A writer reporting a problem could not say which version they had,
and neither could anyone reading the report.

The hash is kept - it answers "exactly which code", which is what a bug report
needs. `PRODUCT_VERSION` answers "which release", which is what a person needs,
and this file holds the packaging files to it so a release cannot ship with
three numbers that disagree.

The .NET host is deliberately NOT included. It is a separate component with its
own lifecycle, shipped inside the product rather than as the product, and
forcing it to share a number would say something untrue about both.
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from echo_link_core.version import PRODUCT_VERSION, build_identity  # noqa: E402


def test_the_version_looks_like_a_version():
    assert re.fullmatch(r"\d+\.\d+\.\d+", PRODUCT_VERSION), (
        f"{PRODUCT_VERSION!r} is not a three-part version, and the Win32 "
        f"VERSIONINFO below needs exactly three parts to build its tuple"
    )


def test_pyproject_agrees():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    found = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert found is not None, "pyproject.toml declares no version"
    assert found.group(1) == PRODUCT_VERSION, (
        f"pyproject.toml says {found.group(1)}, the product says {PRODUCT_VERSION}"
    )


def test_the_installer_agrees():
    text = (ROOT / "packaging" / "EchoLink.iss").read_text(encoding="utf-8")
    found = re.search(r'#define\s+MyAppVersion\s+"([^"]+)"', text)
    assert found is not None, "EchoLink.iss declares no MyAppVersion"
    assert found.group(1) == PRODUCT_VERSION, (
        f"the installer says {found.group(1)}, the product says {PRODUCT_VERSION}. "
        f"Windows shows this one in Apps and Features."
    )


def test_the_executable_metadata_agrees():
    """What right-click > Properties > Details shows on the .exe."""
    text = (ROOT / "packaging" / "manuscript_version_info.txt").read_text(
        encoding="utf-8"
    )
    parts = tuple(int(value) for value in PRODUCT_VERSION.split("."))
    expected_tuple = f"({parts[0]}, {parts[1]}, {parts[2]}, 0)"

    for field in ("filevers", "prodvers"):
        found = re.search(rf"{field}=\(([^)]*)\)", text)
        assert found is not None, f"{field} is missing from the VERSIONINFO"
        assert f"({found.group(1)})" == expected_tuple, (
            f"{field}=({found.group(1)}) does not match {PRODUCT_VERSION}"
        )

    for field in ("FileVersion", "ProductVersion"):
        found = re.search(rf"StringStruct\('{field}',\s*'([^']+)'\)", text)
        assert found is not None, f"{field} is missing from the VERSIONINFO"
        assert found.group(1) == PRODUCT_VERSION, (
            f"{field} says {found.group(1)}, the product says {PRODUCT_VERSION}"
        )


def test_the_identity_carries_both_the_version_and_the_commit():
    """A bug report needs the release AND the exact code behind it."""
    identity = build_identity()
    assert identity.startswith(PRODUCT_VERSION)
    assert "(" in identity and identity.endswith(")")
    commit = identity[identity.index("(") + 1 : -1]
    assert commit, "the identity carries no commit"


def test_diagnostics_reports_the_version():
    """--diagnostics is what a support request pastes, so it carries both."""
    import ast

    tree = ast.parse(
        (ROOT / "src" / "echo_link_app" / "main.py").read_text(encoding="utf-8")
    )
    diagnostics = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "diagnostics"
    )
    called = {
        node.func.id
        for node in ast.walk(diagnostics)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "product_version" in called, (
        "--diagnostics reports a commit hash but no version, so a support "
        "request cannot say which release it came from"
    )
    assert "build_commit" in called, "the exact code is still needed too"