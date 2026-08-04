from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from echo_link_core.correction import deterministic_cleanup


DEFAULT_CORPUS = ROOT / "docs" / "capstone" / "gold_corpus.json"
BASELINE_OUTPUT = ROOT / "docs" / "capstone" / "baseline_results.json"
DEFAULT_OUTPUT = ROOT / "docs" / "capstone" / "current_results.json"


def load_corpus(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if payload.get("schema_version") != 1 or not isinstance(cases, list) or not cases:
        raise ValueError("Corpus must use schema version 1 and contain cases.")
    identifiers = [case.get("id") for case in cases]
    if any(not value for value in identifiers) or len(identifiers) != len(set(identifiers)):
        raise ValueError("Every corpus case needs a unique id.")
    for case in cases:
        for field in ("category", "raw", "expected", "protected_terms"):
            if field not in case:
                raise ValueError(f"Corpus case {case['id']} is missing {field}.")
    return payload


def evaluate_corpus(
    payload: dict[str, Any],
    *,
    repeats: int = 25,
    run_role: str = "current",
) -> dict[str, Any]:
    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    if run_role not in {"baseline", "current"}:
        raise ValueError("run_role must be baseline or current")
    vocabulary = dict(payload.get("default_vocabulary") or {})
    results: list[dict[str, Any]] = []
    timings_ms: list[float] = []

    for case in payload["cases"]:
        case_vocabulary = dict(vocabulary)
        case_vocabulary.update(case.get("vocabulary") or {})
        output = ""
        case_timings: list[float] = []
        for _ in range(repeats):
            started = time.perf_counter_ns()
            output = deterministic_cleanup(case["raw"], case_vocabulary)
            case_timings.append((time.perf_counter_ns() - started) / 1_000_000)
        timings_ms.extend(case_timings)
        missing_protected = [
            term for term in case["protected_terms"] if term not in output
        ]
        results.append(
            {
                "id": case["id"],
                "category": case["category"],
                "output": output,
                "expected": case["expected"],
                "exact_match": output == case["expected"],
                "protected_content_pass": not missing_protected,
                "missing_protected_terms": missing_protected,
                "median_cleanup_latency_ms": round(statistics.median(case_timings), 4),
            }
        )

    total = len(results)
    exact_count = sum(result["exact_match"] for result in results)
    protected_count = sum(result["protected_content_pass"] for result in results)
    sorted_timings = sorted(timings_ms)
    p95_index = min(len(sorted_timings) - 1, int(len(sorted_timings) * 0.95))
    return {
        "schema_version": 2,
        "run_role": run_role,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "corpus_name": payload["name"],
        "automated_result": {
            "case_count": total,
            "exact_match_count": exact_count,
            "exact_match_rate": round(exact_count / total, 4),
            "protected_content_pass_count": protected_count,
            "protected_content_pass_rate": round(protected_count / total, 4),
            "median_cleanup_latency_ms": round(statistics.median(timings_ms), 4),
            "p95_cleanup_latency_ms": round(sorted_timings[p95_index], 4),
            "repeat_count_per_case": repeats,
        },
        "human_metrics": {
            "correction_free_acceptance_rate": None,
            "median_time_to_accepted_text_seconds": None,
            "status": "not_measured_requires_live_user_session",
        },
        "cases": results,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Measure the fixed Manuscript capstone corpus")
    result.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    result.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    result.add_argument("--repeats", type=int, default=25)
    result.add_argument("--no-write", action="store_true")
    result.add_argument("--check", action="store_true")
    result.add_argument(
        "--run-role",
        choices=("baseline", "current"),
        default="current",
        help="Label the evidence as the locked baseline or the current build.",
    )
    result.add_argument(
        "--replace-baseline",
        action="store_true",
        help="Explicitly allow replacing the locked automated baseline evidence.",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    payload = load_corpus(args.corpus)
    report = evaluate_corpus(payload, repeats=args.repeats, run_role=args.run_role)
    report["evidence"] = {
        "code_commit": _git_commit(),
        "corpus_sha256": hashlib.sha256(args.corpus.read_bytes()).hexdigest().upper(),
    }
    if not args.no_write:
        if args.output.resolve() == BASELINE_OUTPUT.resolve() and not args.replace_baseline:
            print(
                "Refusing to overwrite locked baseline_results.json. "
                "Use current_results.json or pass --replace-baseline explicitly.",
                file=sys.stderr,
            )
            return 2
        if args.output.resolve() == BASELINE_OUTPUT.resolve() and args.run_role != "baseline":
            print("Baseline output requires --run-role baseline.", file=sys.stderr)
            return 2
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    result_metrics = report["automated_result"]
    print(
        f"{result_metrics['exact_match_count']}/{result_metrics['case_count']} exact; "
        f"{result_metrics['protected_content_pass_count']}/{result_metrics['case_count']} protected; "
        f"median cleanup {result_metrics['median_cleanup_latency_ms']} ms"
    )
    if args.check and result_metrics["case_count"] != 20:
        print("Capstone corpus must contain exactly 20 cases.", file=sys.stderr)
        return 1
    return 0


def _git_commit() -> str:
    try:
        head = (ROOT / ".git" / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            head = (ROOT / ".git" / head[5:]).read_text(encoding="utf-8").strip()
        return head
    except OSError:
        return "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
