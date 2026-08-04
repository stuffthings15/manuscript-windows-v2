from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from echo_link_core.storage import EchoLinkStore
from echo_link_core.writing_models import (
    LocalWritingModelManager,
    WritingModelError,
    WritingModelInfo,
)


SCHEMA_VERSION = 2
DEFAULT_ENDPOINT = "http://127.0.0.1:11434"
_FULL_COMMIT = re.compile(r"[0-9a-fA-F]{40}")


def collect_evidence(
    manager: LocalWritingModelManager,
    configured_model: str,
    *,
    timeout: float = 120.0,
) -> dict:
    """Collect safe local-model metadata and one bounded benchmark."""
    models = manager.list_models(timeout=min(5.0, timeout))
    selected = _match_configured_model(models, configured_model)
    if selected is None:
        raise WritingModelError(
            f"Configured writing model is not installed locally: {configured_model}"
        )
    benchmark = manager.benchmark(selected.name, timeout=timeout)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "passed" if benchmark.response_verified else "failed",
        "local_only": True,
        "endpoint": manager.endpoint,
        "server_version": manager.server_version(timeout=min(5.0, timeout)),
        "installed_model_count": len(models),
        "configured_model": configured_model,
        "configured_model_found": True,
        "model": asdict(selected),
        "benchmark": asdict(benchmark),
    }


def _match_configured_model(
    models: tuple[WritingModelInfo, ...], configured_model: str
) -> WritingModelInfo | None:
    wanted = (configured_model or "").strip().casefold()
    exact = next((model for model in models if model.name.casefold() == wanted), None)
    if exact is not None or ":" in wanted:
        return exact
    return next(
        (
            model
            for model in models
            if model.name.casefold().split(":", 1)[0] == wanted
        ),
        None,
    )


def _bounded_timeout(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a number") from exc
    if not 5.0 <= value <= 120.0:
        raise argparse.ArgumentTypeError("timeout must be between 5 and 120 seconds")
    return value


def _source_provenance(root: Path = ROOT) -> dict:
    """Read local Git provenance without exposing paths or file names."""
    commands = (
        ["git", "rev-parse", "--verify", "HEAD"],
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
    )
    completed = []
    try:
        for command in commands:
            completed.append(
                subprocess.run(
                    command,
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=10.0,
                    check=False,
                )
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WritingModelError("Git source provenance is unavailable.") from exc
    if any(result.returncode != 0 for result in completed):
        raise WritingModelError("Git source provenance is unavailable.")
    commit = completed[0].stdout.strip()
    if not _FULL_COMMIT.fullmatch(commit):
        raise WritingModelError("Git source provenance returned an invalid commit.")
    return {
        "source_commit": commit.casefold(),
        "source_tree_clean_at_probe_start": not bool(completed[1].stdout.strip()),
    }


def _write_report(report: dict, output: Path | None) -> None:
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if output is None:
        print(rendered, end="")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(output)
    print(f"Writing-model evidence: {output}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify Manuscript's configured writing model through local Ollama."
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--timeout", type=_bounded_timeout, default=120.0)
    args = parser.parse_args(argv)

    configured_model = ""
    provenance: dict = {}
    try:
        provenance = _source_provenance()
        configured_model = EchoLinkStore(args.database).get_settings().ollama_model
        manager = LocalWritingModelManager(args.endpoint)
        report = collect_evidence(
            manager,
            configured_model,
            timeout=args.timeout,
        )
        report.update(provenance)
    except (OSError, ValueError, WritingModelError) as exc:
        report = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "status": "failed",
            "local_only": True,
            "configured_model": configured_model,
            "configured_model_found": False,
            "error": str(exc),
            **provenance,
        }
        _write_report(report, args.output)
        return 1

    _write_report(report, args.output)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
