from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_bootstrap_falls_back_when_py_launcher_returns_failure(tmp_path: Path) -> None:
    project = tmp_path / "project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts" / "bootstrap.ps1", scripts / "bootstrap.ps1")
    model = project / "models" / "base.en" / "model.bin"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"test model marker")

    commands = tmp_path / "commands"
    commands.mkdir()
    (commands / "py.cmd").write_text("@exit /b 1\n", encoding="ascii")
    (commands / "python.cmd").write_text(
        f'@"{sys.executable}" %*\n',
        encoding="ascii",
    )
    env = {**os.environ, "PATH": f"{commands}{os.pathsep}{os.environ['PATH']}"}

    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(scripts / "bootstrap.ps1"),
            "-SkipInstall",
            "-SkipDiagnostics",
        ],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (project / ".venv" / "Scripts" / "python.exe").is_file()
