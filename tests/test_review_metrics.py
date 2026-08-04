from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "export_review_metrics.py"
SPEC = importlib.util.spec_from_file_location("export_review_metrics", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_review_metrics_use_completed_cases_and_accepted_latency():
    metrics = MODULE.summarize_feedback(
        [
            {"decision": "inserted", "edited": 0, "latency_ms": 1000},
            {"decision": "accepted", "edited": 1, "latency_ms": 3000},
            {"decision": "rejected", "edited": 0, "latency_ms": 2000},
            {"decision": "retry", "edited": 0, "latency_ms": 500},
        ]
    )
    assert metrics["completed_case_count"] == 3
    assert metrics["correction_free_acceptance_rate"] == 0.3333
    assert metrics["median_time_to_accepted_text_seconds"] == 2.0
    assert metrics["retry_count"] == 1
    assert metrics["status"] == "needs_20_case_live_session"
