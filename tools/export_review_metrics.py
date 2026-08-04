from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from echo_link_core.storage import EchoLinkStore


DEFAULT_OUTPUT = ROOT / "docs" / "capstone" / "live_review_results.json"
ACCEPTED_DECISIONS = {"accepted", "copied", "inserted"}


def summarize_feedback(rows: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [row for row in rows if row["decision"] != "retry"]
    accepted = [row for row in completed if row["decision"] in ACCEPTED_DECISIONS]
    correction_free = [row for row in accepted if not bool(row["edited"])]
    accepted_latencies = [
        int(row["latency_ms"])
        for row in accepted
        if row.get("latency_ms") is not None
    ]
    return {
        "completed_case_count": len(completed),
        "accepted_case_count": len(accepted),
        "rejected_case_count": sum(row["decision"] == "rejected" for row in completed),
        "retry_count": sum(row["decision"] == "retry" for row in rows),
        "correction_free_acceptance_count": len(correction_free),
        "correction_free_acceptance_rate": (
            round(len(correction_free) / len(completed), 4) if completed else None
        ),
        "median_time_to_accepted_text_seconds": (
            round(statistics.median(accepted_latencies) / 1000, 3)
            if accepted_latencies
            else None
        ),
        "status": "complete" if len(completed) >= 20 else "needs_20_case_live_session",
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Export private Manuscript review metrics")
    result.add_argument("--database", type=Path)
    result.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    result.add_argument("--last", type=int, default=20)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    store = EchoLinkStore(args.database) if args.database else EchoLinkStore()
    rows = list(reversed(store.list_review_feedback(limit=max(1, args.last))))
    report = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "privacy": "Contains decision, edit, mode, and timing metrics only. No transcript or audio.",
        "metrics": summarize_feedback(rows),
        "events": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    metrics = report["metrics"]
    print(
        f"completed={metrics['completed_case_count']} "
        f"correction_free_rate={metrics['correction_free_acceptance_rate']} "
        f"median_seconds={metrics['median_time_to_accepted_text_seconds']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
