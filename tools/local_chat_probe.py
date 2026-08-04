from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from echo_link_core.local_chat import ApprovedSourceChat, LocalChatResult
from echo_link_core.local_sources import LocalSourceIndex
from echo_link_core.storage import EchoLinkStore


SCHEMA_VERSION = 1
DEFAULT_ENDPOINT = "http://127.0.0.1:11434"
_FULL_COMMIT = re.compile(r"[0-9a-fA-F]{40}")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class ProbeVerificationError(RuntimeError):
    """A stable, content-free verification failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ProbeMaterial:
    source_text: str
    question: str
    expected_fact: str


def _new_probe_material() -> ProbeMaterial:
    token = secrets.token_hex(16)
    return ProbeMaterial(
        source_text=(
            "# Temporary approved-source verification\n\n"
            f"The approved source verification code is {token}.\n"
            "This code belongs only to this temporary local source.\n"
        ),
        question="What is the approved source verification code? Cite the source.",
        expected_fact=token,
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _is_loopback_endpoint(endpoint: str) -> bool:
    value = (endpoint or "").strip().rstrip("/")
    parsed = urlparse(value)
    try:
        parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme == "http"
        and (parsed.hostname or "").casefold() in _LOOPBACK_HOSTS
        and not parsed.username
        and not parsed.password
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )


def _citation_digest(result: LocalChatResult) -> str:
    material = [
        {
            "excerpt": citation.excerpt,
            "line_number": citation.line_number,
            "source_id": citation.source_id,
        }
        for citation in result.citations
    ]
    return _sha256_text(
        json.dumps(material, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    )


def collect_evidence(
    chat,
    configured_model: str,
    *,
    timeout: float = 45.0,
    material: ProbeMaterial | None = None,
    temp_parent: Path | None = None,
) -> dict:
    """Run one ephemeral approved-source question and return content-free evidence."""

    model = (configured_model or "").strip()
    if not model:
        raise ProbeVerificationError("configured_model_missing")
    endpoint = str(getattr(chat, "endpoint", ""))
    if not _is_loopback_endpoint(endpoint):
        raise ProbeVerificationError("non_loopback_endpoint")

    probe_material = material or _new_probe_material()
    source_bytes = probe_material.source_text.encode("utf-8")
    started_ns = time.perf_counter_ns()
    source_path: Path | None = None
    source_index = LocalSourceIndex()

    try:
        with tempfile.TemporaryDirectory(
            prefix="echo-link-local-chat-",
            dir=temp_parent,
        ) as directory:
            source_path = Path(directory) / "approved-source.md"
            source_path.write_bytes(source_bytes)
            approval = source_index.approve([source_path])
            if (
                len(approval) != 1
                or not approval[0].indexed
                or source_index.approved_paths != (str(source_path.resolve()),)
            ):
                raise ProbeVerificationError("temporary_source_approval_failed")

            request_started_ns = time.perf_counter_ns()
            result = chat.ask(
                probe_material.question,
                sources=source_index,
                model=model,
                timeout=timeout,
            )
            request_finished_ns = time.perf_counter_ns()

            expected_path = str(source_path.resolve())
            citations = tuple(result.citations)
            structured_s1 = tuple(
                citation
                for citation in citations
                if citation.source_id == "S1"
                and citation.path == expected_path
                and citation.line_number > 0
                and bool(citation.excerpt.strip())
            )
            if not citations or len(structured_s1) != 1:
                raise ProbeVerificationError("structured_s1_citation_missing")
            if any(citation.path != expected_path for citation in citations):
                raise ProbeVerificationError("unapproved_citation_returned")
            if result.model != model:
                raise ProbeVerificationError("configured_model_mismatch")

            answer = result.answer.strip()
            s1_marker_count = answer.count("[S1]")
            if s1_marker_count < 1:
                raise ProbeVerificationError("answer_s1_marker_missing")
            if probe_material.expected_fact.casefold() not in answer.casefold():
                raise ProbeVerificationError("expected_fact_missing")

            evidence = {
                "schema_version": SCHEMA_VERSION,
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "status": "passed",
                "hashes": {
                    "answer_sha256": _sha256_text(answer),
                    "citation_set_sha256": _citation_digest(result),
                    "configured_model_sha256": _sha256_text(model),
                    "endpoint_sha256": _sha256_text(endpoint),
                    "expected_fact_sha256": _sha256_text(probe_material.expected_fact),
                    "question_sha256": _sha256_text(probe_material.question),
                    "source_sha256": _sha256_bytes(source_bytes),
                },
                "counts": {
                    "answer_character_count": len(answer),
                    "answer_s1_marker_count": s1_marker_count,
                    "approved_source_count": len(source_index.approved_paths),
                    "citation_count": len(citations),
                    "question_character_count": len(probe_material.question),
                    "source_byte_count": len(source_bytes),
                    "source_line_count": len(probe_material.source_text.splitlines()),
                    "structured_s1_citation_count": len(structured_s1),
                },
                "timing_ms": {
                    "model_round_trip": round(
                        (request_finished_ns - request_started_ns) / 1_000_000,
                        3,
                    ),
                    "total": round(
                        (time.perf_counter_ns() - started_ns) / 1_000_000,
                        3,
                    ),
                },
                "provenance": {
                    "answer_expected_fact_verified": True,
                    "answer_s1_marker_verified": True,
                    "configured_model_used": True,
                    "local_only": True,
                    "loopback_endpoint_verified": True,
                    "structured_s1_citation_verified": True,
                    "temporary_markdown_source": True,
                },
            }
    finally:
        source_index.clear()

    if source_path is None or source_path.exists():
        raise ProbeVerificationError("temporary_source_cleanup_failed")
    evidence["provenance"]["temporary_source_removed"] = True
    return evidence


def _bounded_timeout(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a number") from exc
    if not 5.0 <= value <= 120.0:
        raise argparse.ArgumentTypeError("timeout must be between 5 and 120 seconds")
    return value


def _source_provenance(root: Path = ROOT) -> dict:
    """Read Git provenance without retaining paths or changed-file names."""

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
        raise ProbeVerificationError("git_provenance_unavailable") from exc
    if any(result.returncode != 0 for result in completed):
        raise ProbeVerificationError("git_provenance_unavailable")
    commit = completed[0].stdout.strip()
    if not _FULL_COMMIT.fullmatch(commit):
        raise ProbeVerificationError("git_provenance_invalid")
    return {
        "source_commit": commit.casefold(),
        "source_tree_clean_at_probe_start": not bool(completed[1].stdout.strip()),
    }


def _failure_report(
    *,
    configured_model: str,
    stage: str,
    error: BaseException,
    provenance: dict,
    started_ns: int,
) -> dict:
    model = (configured_model or "").strip()
    hashes = {"failure_type_sha256": _sha256_text(type(error).__name__)}
    if model:
        hashes["configured_model_sha256"] = _sha256_text(model)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "failed",
        "hashes": hashes,
        "counts": {},
        "timing_ms": {
            "total": round((time.perf_counter_ns() - started_ns) / 1_000_000, 3)
        },
        "provenance": {
            "failure_stage": stage,
            "local_only": True,
            **provenance,
        },
    }


def _write_report(report: dict, output: Path) -> None:
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    destination = output.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Local source chat evidence: {destination}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify approved-source chat with Manuscript's configured loopback Ollama "
            "model and write content-free JSON evidence."
        )
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--timeout", type=_bounded_timeout, default=45.0)
    args = parser.parse_args(argv)

    started_ns = time.perf_counter_ns()
    configured_model = ""
    provenance: dict = {}
    stage = "source_provenance"
    try:
        provenance = _source_provenance()
        stage = "configured_model"
        configured_model = EchoLinkStore(args.database).get_settings().ollama_model
        stage = "loopback_chat"
        chat = ApprovedSourceChat(args.endpoint)
        stage = "live_chat_probe"
        report = collect_evidence(
            chat,
            configured_model,
            timeout=args.timeout,
        )
        report["provenance"].update(provenance)
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        report = _failure_report(
            configured_model=configured_model,
            stage=stage,
            error=exc,
            provenance=provenance,
            started_ns=started_ns,
        )

    _write_report(report, args.output)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
