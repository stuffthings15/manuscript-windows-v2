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
        str(ROOT / "run_echo_link.py"),
        *[value for value in args.app_arguments if value != "--"],
    ]
    print(" ".join(command), flush=True)
    if args.wait:
        return subprocess.run(command, cwd=ROOT, env=environment).returncode
    subprocess.Popen(command, cwd=ROOT, env=environment)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
