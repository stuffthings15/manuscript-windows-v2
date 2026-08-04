from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "native" / "polish-host" / "Manuscript.PolishHost.csproj"
OUTPUT = ROOT / "build" / "generated" / "polish-host"
EXECUTABLE = OUTPUT / "Manuscript.PolishHost.exe"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the self-contained Manuscript Polish host"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    command = [
        "dotnet",
        "publish",
        str(PROJECT),
        "--configuration",
        "Release",
        "--runtime",
        "win-x64",
        "--self-contained",
        "true",
        "--output",
        str(OUTPUT),
        "/p:PublishSingleFile=true",
        "/p:DebugType=None",
        "/p:DebugSymbols=false",
    ]
    if args.dry_run:
        print(" ".join(command))
        return 0
    if shutil.which("dotnet") is None:
        raise SystemExit("The .NET SDK is required to build the Polish host.")
    subprocess.run(command, cwd=ROOT, check=True)
    if not EXECUTABLE.is_file() or EXECUTABLE.stat().st_size < 1024 * 1024:
        raise SystemExit("The self-contained Polish host was not created.")
    print(EXECUTABLE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
