r"""Two ways a managed Windows machine stopped Manuscript launching at all.

Neither is exotic. Both are ordinary corporate configuration, and both turned
into "the product does not work" rather than "this one feature does not".

1. The autostart entry. `configure_startup` runs unconditionally on every start
   and writes to HKCU\...\Run. Under a policy that denies that write it raised
   PermissionError into the startup handler in main.py, which reported
   "Manuscript could not start". A writer lost the whole product over a
   preference that defaults to OFF and that they may never have set.

2. The data folder. `data_root()` refuses a network path, and refuses any path
   containing a symlink or junction. A profile folder-redirected to a share, or
   a user profile junctioned onto another volume, both hit that. It is resolved
   before anything else - `configure_logging()` goes through it - so the
   failure landed OUTSIDE the startup handler and produced a raw traceback in a
   console a desktop user does not have. There was no log to point them at
   either, because the log file is what could not be opened.
"""
from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from echo_link_app import startup  # noqa: E402


def test_a_denied_registry_write_does_not_stop_the_app(monkeypatch, caplog):
    """The whole point: refusing the key must not refuse the product."""

    def _denied(_enabled):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(startup, "_write_startup_entry", _denied)
    monkeypatch.setattr(startup.os, "name", "nt")

    result = startup.configure_startup(True)

    assert result is False, "a denied write should report failure, not raise"


def test_the_denied_write_is_not_swallowed_silently(monkeypatch, caplog):
    """A setting that will not stick must leave the writer something to point at."""
    import logging

    def _denied(_enabled):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(startup, "_write_startup_entry", _denied)
    monkeypatch.setattr(startup.os, "name", "nt")

    with caplog.at_level(logging.WARNING, logger="echo_link_app.startup"):
        startup.configure_startup(True)

    assert "startup entry" in caplog.text
    assert "Access is denied" in caplog.text


def test_a_successful_write_still_reports_success(monkeypatch):
    monkeypatch.setattr(startup, "_write_startup_entry", lambda enabled: True)
    monkeypatch.setattr(startup.os, "name", "nt")
    assert startup.configure_startup(True) is True


def test_the_data_folder_is_resolved_inside_a_guard():
    """The failure must reach the dialog, not the console.

    Parsed rather than run, because reaching it for real needs a redirected
    profile. The property asserted is structural: the three calls that touch
    the data folder sit inside a try, and its handler explains rather than
    re-raises.
    """
    tree = ast.parse(
        (ROOT / "src" / "echo_link_app" / "main.py").read_text(encoding="utf-8")
    )
    run = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_run_application"
    )
    first = run.body[0]
    assert isinstance(first, ast.Try), (
        "_run_application no longer opens with a guard. configure_logging() "
        "resolves the log path through data_root(), so an unusable data folder "
        "crashes before the startup handler can report it."
    )
    guarded = {
        node.func.id
        for node in ast.walk(first)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "data_root" in guarded, "data_root() is resolved outside the guard"
    assert "configure_logging" in guarded, "configure_logging() is outside the guard"

    handlers = first.handlers
    assert handlers, "the guard catches nothing"
    explains = {
        node.func.id
        for handler in handlers
        for node in ast.walk(handler)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "_explain_unusable_data_folder" in explains, (
        "the guard does not explain the failure to the writer"
    )


def test_the_explanation_does_not_promise_a_log_that_does_not_exist():
    """The log is the thing that could not be opened, so it cannot be the advice."""
    import inspect

    from echo_link_app import main as app_main

    message = inspect.getsource(app_main._explain_unusable_data_folder)
    body = message.split('"""')[2]  # after the docstring
    assert "log" not in body.lower(), (
        "the message points the writer at a log file, which is exactly what "
        "could not be opened on this path"
    )
