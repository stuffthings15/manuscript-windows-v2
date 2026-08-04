from __future__ import annotations

import csv
import io
import json
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .data_protection import LocalExportGuard, sanitize_export_payload, validate_local_directory
from .paths import data_root, resource_path


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    id: str
    category: str
    prompt: str


def load_evaluation_cases(path: str | Path | None = None) -> list[EvaluationCase]:
    source = Path(path) if path else resource_path("capstone", "gold_corpus.json")
    if path is None and not source.exists():
        source = resource_path("docs", "capstone", "gold_corpus.json")
    payload = json.loads(source.read_text(encoding="utf-8"))
    return [
        EvaluationCase(str(row["id"]), str(row["category"]), str(row["raw"]))
        for row in payload["cases"]
    ]


def summarize_evaluation(trials: list[dict[str, Any]], case_count: int = 20) -> dict[str, Any]:
    completed = [row for row in trials if row["decision"] in {"accepted", "rejected"}]
    accepted = [row for row in completed if row["decision"] == "accepted"]
    correction_free = [row for row in accepted if not bool(row["edited"])]
    latencies = [int(row["latency_ms"]) for row in accepted if row["latency_ms"] is not None]
    return {
        "completed_case_count": len(completed),
        "accepted_case_count": len(accepted),
        "rejected_case_count": len(completed) - len(accepted),
        "retry_count": sum(row["decision"] == "retry" for row in trials),
        "correction_free_acceptance_count": len(correction_free),
        "correction_free_acceptance_rate": (
            round(len(correction_free) / len(completed), 4) if completed else None
        ),
        "median_time_to_accepted_text_seconds": (
            round(statistics.median(latencies) / 1000, 3) if latencies else None
        ),
        "status": "complete" if len(completed) >= case_count else "in_progress",
    }


def export_evaluation_run(store, run_id: int, output_dir: str | Path | None = None) -> tuple[Path, Path]:
    run = store.get_evaluation_run(run_id)
    if not run:
        raise ValueError("Evaluation run was not found.")
    trials = store.list_evaluation_trials(run_id)
    destination = validate_local_directory(
        Path(output_dir) if output_dir else data_root() / "capstone-evaluations",
        purpose="Evaluation export folder",
        create=True,
    )
    stem = f"{run['kind']}-run-{run_id}"
    json_path = destination / f"{stem}.json"
    csv_path = destination / f"{stem}.csv"
    report = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "privacy": "Synthetic capstone outputs and local timing only. No audio.",
        "run": run,
        "metrics": summarize_evaluation(trials),
        "trials": trials,
    }
    guard = LocalExportGuard()
    guard.write_json(
        json_path,
        "capstone-evaluation",
        report,
        contains_user_text=True,
        overwrite=True,
    )
    safe_trials, _redacted = sanitize_export_payload(trials)
    handle = io.StringIO(newline="")
    writer = csv.DictWriter(
        handle,
        fieldnames=[
            "case_id", "trial", "started_at_utc", "output_ready_at_utc",
            "accepted_at_utc", "accepted_without_edit", "edit_count",
            "final_text", "notes",
        ],
    )
    writer.writeheader()
    for row in safe_trials:
        writer.writerow(
            {
                "case_id": row["case_id"],
                "trial": row["trial"],
                "started_at_utc": row["started_at"],
                "output_ready_at_utc": row["output_ready_at"] or "",
                "accepted_at_utc": row["accepted_at"] or "",
                "accepted_without_edit": int(
                    row["decision"] == "accepted" and not bool(row["edited"])
                ),
                "edit_count": int(bool(row["edited"])),
                "final_text": row["final_text"],
                "notes": row["notes"],
            }
        )
    guard.write_text(
        csv_path,
        "capstone-evaluation-csv",
        handle.getvalue(),
        contains_user_text=True,
        allowed_suffixes={".csv"},
        overwrite=True,
    )
    return json_path, csv_path
