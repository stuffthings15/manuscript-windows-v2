"""The script the harness launches must be able to start the app.

tools/run_dev_app.py launched run_echo_link.py from the baseline commit
d66e0b6 until 2026-09-30. That file does "from echo_link_app.main import main"
without putting src/ on sys.path, so it dies immediately:

    ModuleNotFoundError: No module named 'echo_link_app'

run_dev_app launches it through pythonw.exe, which has no console, so the
traceback went nowhere. The only symptom anyone ever saw was
restart_app_and_wait printing "timed out waiting for the app to report a
loaded model" - which run_dev_app's own 2026-08-20 comment warns "reads as a
broken corrector rather than a blocked launcher".

The cost: tools/run_essays_batch.py could never start the app in this project,
so no tone has ever been measured in Word. Its --tone help text says exactly
that - "is why no tone number ever came from Word" - and it was read as a
statement about tone selection rather than about the launcher.

tests/test_restart_app_evidence.py could not catch this: it monkeypatches
subprocess.Popen away, so the command is never examined and certainly never
run. A test that replaces the launch cannot discover that the launch is
broken.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


def _launched_script() -> str:
    """The entry point tools/run_dev_app.py hands to the interpreter."""
    text = (ROOT / "tools" / "run_dev_app.py").read_text(encoding="utf-8")
    found = re.findall(r'str\(ROOT / "(run_[a-z_]+\.py)"\)', text)
    assert found, "run_dev_app.py no longer names an entry point this way"
    return found[0]


def test_the_launcher_names_an_entry_point_that_exists():
    assert (ROOT / _launched_script()).is_file()


def test_the_launched_entry_point_can_import_the_app():
    """The defect itself, and the only check that would have caught it.

    --help exercises the whole import chain and exits without opening a
    window. run_echo_link.py exits 1 with ModuleNotFoundError here;
    run_manuscript.py exits 0.
    """
    interpreter = VENV_PYTHON if VENV_PYTHON.is_file() else pathlib.Path(sys.executable)
    result = subprocess.run(
        [str(interpreter), str(ROOT / _launched_script()), "--help"],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, (
        f"{_launched_script()} cannot start the app.\n"
        f"stderr: {result.stderr[-600:]}"
    )
    assert "ModuleNotFoundError" not in result.stderr


def test_the_process_matcher_follows_the_launcher():
    """APP_SCRIPT must name whatever run_dev_app launches.

    A matcher that does not follow the launcher cannot find the running app:
    stop_the_running_app reports nothing stopped, the replacement instance
    hits the single-instance mutex and exits silently, and the wait times out
    exactly as if the app were broken. That happened on 2026-09-30 while
    chasing this same bug.
    """
    from tools import restart_app_and_wait as restart

    assert restart.APP_SCRIPT == _launched_script().removesuffix(".py")


def test_stop_the_running_app_matches_on_the_same_name():
    """The literal inside the function must not drift from APP_SCRIPT.

    It was a second hard-coded copy until 2026-09-30.
    """
    from tools import restart_app_and_wait as restart

    source = pathlib.Path(restart.__file__).read_text(encoding="utf-8")
    body = source[source.index("def stop_the_running_app") :]
    body = body[: body.index("\ndef ")] if "\ndef " in body[10:] else body
    assert 'script = "run_' not in body, (
        "stop_the_running_app hard-codes a script name again; use APP_SCRIPT"
    )
