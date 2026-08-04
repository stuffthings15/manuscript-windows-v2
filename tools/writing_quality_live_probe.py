from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from echo_link_core.selected_text import LocalWritingAssistant
from echo_link_core.storage import EchoLinkStore
from echo_link_core.writing_quality import (
    WritingQualityCorpus,
    evaluate_writing_quality,
    load_writing_quality_corpus,
)


SCHEMA_VERSION = 1
DEFAULT_ENDPOINT = "http://127.0.0.1:11434"
DEFAULT_CORPUS = ROOT / "benchmarks" / "writing_quality_v1.json"
DEFAULT_OUTPUT = ROOT / "evidence" / "writing-quality-live.json"
_FULL_COMMIT = re.compile(r"[0-9a-fA-F]{40}")
_WORD = re.compile(r"\b[\w'-]+\b", re.UNICODE)


class ProbeVerificationError(RuntimeError):
    """A stable, content-free probe failure."""

    def __init__(self, code: str, *, case_id: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.case_id = case_id


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_exact_loopback_endpoint(endpoint: str) -> bool:
    value = (endpoint or "").strip().rstrip("/")
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme == "http"
        and parsed.hostname == "127.0.0.1"
        and port is not None
        and not parsed.username
        and not parsed.password
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )


def collect_evidence(
    assistant: LocalWritingAssistant,
    configured_model: str,
    corpus: WritingQualityCorpus,
    *,
    timeout: float = 45.0,
) -> dict:
    """Run the locked corpus through one configured loopback writing model."""

    model = (configured_model or "").strip()
    if not model:
        raise ProbeVerificationError("configured_model_missing")
    endpoint = str(getattr(assistant, "endpoint", ""))
    if not _is_exact_loopback_endpoint(endpoint):
        raise ProbeVerificationError("endpoint_not_127_0_0_1")

    responses: dict[str, str] = {}
    execution_failures: dict[str, str] = {}
    for case in corpus.cases:
        try:
            result = assistant.run(
                case.source,
                command=case.command,
                model=model,
                language=case.target_language,
                profile=case.profile,
                protected_terms=tuple(case.checks.get("protected_terms") or ()),
                timeout=timeout,
            )
            if (
                result.model != model
                or result.command != case.command
                or result.profile != case.profile
            ):
                raise ProbeVerificationError("writing_result_metadata_mismatch")
            responses[case.identifier] = result.text
        except (OSError, TypeError, ValueError, RuntimeError) as exc:
            execution_failures[case.identifier] = type(exc).__name__

    evaluation = evaluate_writing_quality(corpus, responses)
    evaluated = {result["id"]: result for result in evaluation["cases"]}
    safe_cases = []
    response_digests = []
    completed_count = 0
    passed_count = 0
    reference_match_count = 0
    quality_failed_count = 0
    for case in corpus.cases:
        if case.identifier in execution_failures:
            safe_cases.append(
                {
                    "id": case.identifier,
                    "status": "execution_failed",
                    "checks": {
                        "execution_completed": False,
                        "quality_checks_completed": False,
                    },
                    "hashes": {
                        "failure_type_sha256": _sha256_text(
                            execution_failures[case.identifier]
                        )
                    },
                    "counts": {
                        "attempt_count": 1,
                        "failed_check_count": 0,
                    },
                }
            )
            continue

        result = evaluated[case.identifier]
        response = responses[case.identifier]
        response_digest = _sha256_text(response)
        response_digests.append(response_digest)
        completed_count += 1
        passed_count += int(result["passed"])
        reference_match_count += int(result["reference_match"])
        quality_failed_count += int(not result["passed"])
        safe_cases.append(
            {
                "id": case.identifier,
                "status": "passed" if result["passed"] else "failed_checks",
                "checks": {
                    "execution_completed": True,
                    "quality_checks_completed": True,
                    **dict(result["checks"]),
                },
                "hashes": {"response_sha256": response_digest},
                "counts": {
                    "attempt_count": 1,
                    "character_count": len(response),
                    "utf8_byte_count": len(response.encode("utf-8")),
                    "word_count": len(_WORD.findall(response)),
                    "failed_check_count": len(result["failed_checks"]),
                },
            }
        )

    case_count = len(corpus.cases)
    execution_failed_count = len(execution_failures)
    all_passed = completed_count == case_count and passed_count == case_count
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "passed" if all_passed else "failed",
        "checks": {
            "all_case_executions_completed": execution_failed_count == 0,
            "all_fixed_cases_passed": all_passed,
            "configured_model_used": True,
            "content_free_evidence": True,
            "locked_corpus_used": True,
            "local_only": True,
            "loopback_127_0_0_1_verified": True,
            "no_cloud_fallback": True,
        },
        "hashes": {
            "configured_model_sha256": _sha256_text(model),
            "corpus_sha256": corpus.sha256.casefold(),
            "endpoint_sha256": _sha256_text(endpoint),
            "response_set_sha256": _sha256_text("\n".join(response_digests)),
        },
        "counts": {
            "case_count": case_count,
            "completed_case_count": completed_count,
            "execution_failed_count": execution_failed_count,
            "failed_count": case_count - passed_count,
            "passed_count": passed_count,
            "quality_failed_count": quality_failed_count,
            "reference_match_count": reference_match_count,
        },
        "cases": safe_cases,
    }


def _bounded_timeout(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a number") from exc
    if not 5.0 <= value <= 120.0:
        raise argparse.ArgumentTypeError("timeout must be between 5 and 120 seconds")
    return value


def _source_provenance(root: Path = ROOT) -> dict:
    """Read Git provenance without keeping paths or changed-file names."""

    results = []
    try:
        for command in (
            ["git", "rev-parse", "--verify", "HEAD"],
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        ):
            results.append(
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
    if any(result.returncode != 0 for result in results):
        raise ProbeVerificationError("git_provenance_unavailable")
    commit = results[0].stdout.strip()
    if not _FULL_COMMIT.fullmatch(commit):
        raise ProbeVerificationError("git_provenance_invalid")
    return {
        "source_commit": commit.casefold(),
        "source_tree_clean_at_probe_start": not bool(results[1].stdout.strip()),
    }


def _failure_report(
    *,
    error: BaseException,
    stage: str,
    provenance: dict,
    configured_model: str,
) -> dict:
    code = error.code if isinstance(error, ProbeVerificationError) else type(error).__name__
    case_id = error.case_id if isinstance(error, ProbeVerificationError) else ""
    hashes = {
        "failure_code_sha256": _sha256_text(code),
        "failure_type_sha256": _sha256_text(type(error).__name__),
    }
    model = configured_model.strip()
    if model:
        hashes["configured_model_sha256"] = _sha256_text(model)
    if case_id:
        hashes["failed_case_id_sha256"] = _sha256_text(case_id)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "failed",
        "checks": {
            "content_free_evidence": True,
            "local_only": True,
            "no_cloud_fallback": True,
        },
        "hashes": hashes,
        "counts": {"completed_case_count": 0},
        "provenance": {"failure_stage": stage, **provenance},
        "cases": [],
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
    print(f"Writing-quality evidence: {destination}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run Manuscript's locked writing corpus through the configured local model "
            "and write content-free evidence."
        )
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--timeout", type=_bounded_timeout, default=45.0)
    args = parser.parse_args(argv)

    provenance: dict = {}
    configured_model = ""
    stage = "source_provenance"
    try:
        provenance = _source_provenance()
        if not provenance["source_tree_clean_at_probe_start"]:
            raise ProbeVerificationError("source_tree_dirty")
        stage = "locked_corpus"
        corpus = load_writing_quality_corpus(args.corpus)
        if args.corpus.resolve() != DEFAULT_CORPUS.resolve():
            raise ProbeVerificationError("non_default_corpus_rejected")
        stage = "configured_model"
        configured_model = EchoLinkStore(args.database).get_settings().ollama_model
        stage = "live_writing_quality"
        report = collect_evidence(
            LocalWritingAssistant(args.endpoint),
            configured_model,
            corpus,
            timeout=args.timeout,
        )
        report["provenance"] = provenance
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        report = _failure_report(
            error=exc,
            stage=stage,
            provenance=provenance,
            configured_model=configured_model,
        )

    _write_report(report, args.output)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
