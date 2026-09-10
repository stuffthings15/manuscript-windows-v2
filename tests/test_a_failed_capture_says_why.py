"""A capture that gives up must leave evidence behind.

Every outer handler in `windows_selection.py` was `except Exception: return
None` with no logging. On an end user's machine a UI Automation failure - a COM
apartment problem, security software, an import that did not load - produced the
HUD's "No text was captured" card and NOTHING in echo-link.log. Support would
have a screenshot and no evidence, on a product that deliberately reports
nothing outward and whose local log is therefore the only diagnostic there is.

That contradicts this repository's own rule in CLAUDE.md: "Never swallow
silently. An error is handled, translated into a domain error, logged with
enough context to diagnose it, or propagated."

The nine paths covered are the OUTER handler of each public read method, not the
inner candidate loops - logging every failed control in a tree walk would flood
the log and hide the failures that matter.
"""
from __future__ import annotations

import ast
import logging
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from echo_link_core import windows_selection  # noqa: E402

# The public read paths whose failure the writer sees as "No text was captured".
GUARDED = (
    "read",
    "has_editable_focus",
    "read_editable_focus",
    "read_document",
    "read_page",
    "text_rectangles",
    "is_record_control_focused",
    "restore_selection",
    "poll_once",
)


def test_the_reason_names_the_path_and_the_failure(caplog):
    with caplog.at_level(logging.INFO, logger="echo_link_core.windows_selection"):
        windows_selection._log_capture_failure(
            "read_document", OSError("the tree went away")
        )
    message = caplog.text
    assert "read_document" in message, "the log does not say which path gave up"
    assert "OSError" in message, "the log does not say what went wrong"
    assert "the tree went away" in message


def test_the_captured_text_is_never_logged():
    """The privacy rule outranks the diagnostic.

    Only the exception type and its message are recorded. The types raised on
    these paths - COMError, OSError, uiautomation's own - do not carry document
    content, and nothing here passes any.

    The BODY is inspected, not the source text, so the docstring explaining the
    rule cannot satisfy or violate it.
    """
    tree = ast.parse(
        (ROOT / "src" / "echo_link_core" / "windows_selection.py").read_text(
            encoding="utf-8"
        )
    )
    helper = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_log_capture_failure"
    )
    body = [node for node in helper.body if not isinstance(node, ast.Expr)]
    names = {
        node.attr
        for statement in body
        for node in ast.walk(statement)
        if isinstance(node, ast.Attribute)
    } | {
        node.id
        for statement in body
        for node in ast.walk(statement)
        if isinstance(node, ast.Name)
    }
    forbidden = {"text", "Text", "record", "captured", "detected", "value"}
    leaked = names & forbidden
    assert not leaked, (
        f"_log_capture_failure touches {sorted(leaked)}; a capture failure must "
        f"never put the writer's text in the log"
    )
    # And positively: the only things it reads are the two it is given.
    assert {"where", "exc", "logger", "type"} >= (names - {"info", "__name__"})


def test_every_public_capture_path_reports_its_failure():
    """The rule, checked against the file rather than a list that can rot.

    Each name below is the outer handler of a public read method. If one stops
    reporting, an end-user failure on that path becomes invisible again.
    """
    tree = ast.parse(
        (ROOT / "src" / "echo_link_core" / "windows_selection.py").read_text(
            encoding="utf-8"
        )
    )
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }
    silent = []
    for name in GUARDED:
        node = functions.get(name)
        assert node is not None, f"{name} no longer exists in windows_selection"
        reports = any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "_log_capture_failure"
            for call in ast.walk(node)
        )
        if not reports:
            silent.append(name)
    assert not silent, (
        f"These capture paths swallow their failure with no log: {silent}. On a "
        f"user machine that is a 'No text was captured' card and an empty log."
    )
