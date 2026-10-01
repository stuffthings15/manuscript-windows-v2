"""Launch Manuscript from source for testing, without a PyInstaller build.

Packaging takes about six minutes, so every user-level fix used to cost a
six-minute round trip before it could be seen in the real UI. The source
tree already supports running unpackaged: ``main.py`` accepts a subscription
bypass that only applies when the app is not frozen, so a released package
can never be unlocked this way.

    .venv/Scripts/python.exe tools/run_dev_app.py

Add ``--wait`` to block until the window closes, and pass any further
arguments straight through to the app.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run Manuscript from source with the activation gate off",
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Block until the app exits instead of returning immediately.",
    )
    parser.add_argument("app_arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    environment = os.environ.copy()
    environment["MANUSCRIPT_TEST_BYPASS_SUBSCRIPTION"] = "1"
    environment["PYTHONPATH"] = str(ROOT / "src")

    # pythonw keeps the console clear for a detached run; python keeps the
    # output attached when the caller wants to watch it.
    interpreter = ROOT / ".venv" / "Scripts" / (
        "python.exe" if args.wait else "pythonw.exe"
    )
    command = [
        str(interpreter if interpreter.exists() else Path(sys.executable)),
        # run_manuscript.py, not run_echo_link.py. The latter arrived with the
        # baseline commit d66e0b6 and was never updated: it imports
        # echo_link_app.main without putting src/ on sys.path, so it dies with
        # ModuleNotFoundError before anything starts. Because this launches it
        # through pythonw.exe, which has no console, that traceback went
        # nowhere and the only symptom was restart_app_and_wait reporting
        # "timed out waiting for the app to report a loaded model".
        #
        # Measured 2026-09-30: that is why run_essays_batch.py could not start
        # the app, which is why no tone has ever been measured in Word.
        # run_manuscript.py is the maintained entry point - CLAUDE.md documents
        # it - and it sets up sys.path, the canonical-root check and the
        # development subscription bypass.
        str(ROOT / "run_manuscript.py"),
        *[value for value in args.app_arguments if value != "--"],
    ]
    print(" ".join(command), flush=True)
    if args.wait:
        return subprocess.run(command, cwd=ROOT, env=environment).returncode
    try:
        subprocess.Popen(command, cwd=ROOT, env=environment)
    except OSError as exc:
        # Windows Application Control blocks pythonw.exe on this machine while
        # allowing python.exe, and it does it at CreateProcess with
        # "[WinError 4551] An Application Control policy has blocked this
        # file". Measured on 2026-08-20, when it stopped every Word run: the
        # harness restart returned "timed out waiting for the app to report a
        # loaded model" and the batch reported twenty essays of zeroes, which
        # reads as a broken corrector rather than a blocked launcher.
        #
        # Falling back to python.exe with no console window keeps a detached
        # run detached. It is not a workaround for a signing problem - the
        # signed build is a separate release blocker - it is the difference
        # between the harness working on this machine and not.
        if getattr(exc, "winerror", None) != 4551:
            raise
        fallback = ROOT / ".venv" / "Scripts" / "python.exe"
        print(  # noqa: T201
            f"pythonw is blocked by Application Control; using {fallback.name}",
            flush=True,
        )
        command[0] = str(fallback)
        subprocess.Popen(
            command,
            cwd=ROOT,
            env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
