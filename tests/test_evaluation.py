from __future__ import annotations

import csv
import json

from echo_link_core.evaluation import export_evaluation_run, load_evaluation_cases, summarize_evaluation
from echo_link_core.storage import EchoLinkStore


def test_fixed_capstone_corpus_loads_twenty_ordered_cases():
    cases = load_evaluation_cases()
    assert len(cases) == 20
    assert cases[0].id == "prose-01"
    assert cases[-1].id == "developer-03"


def test_evaluation_run_records_trials_metrics_and_private_exports(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    run_id = store.start_evaluation_run("baseline", "abc123")
    first = store.start_evaluation_trial(run_id, "prose-01")
    store.mark_evaluation_output_ready(first)
    store.complete_evaluation_trial(first, "retry")
    second = store.start_evaluation_trial(run_id, "prose-01")
    store.mark_evaluation_output_ready(second)
    store.complete_evaluation_trial(
        second,
        "accepted",
        edited=False,
        final_text="Hello there.",
        notes="Bearer PRIVATE_EVALUATION_TOKEN",
    )
    trials = store.list_evaluation_trials(run_id)
    metrics = summarize_evaluation(trials)
    assert metrics["completed_case_count"] == 1
    assert metrics["retry_count"] == 1
    assert metrics["correction_free_acceptance_rate"] == 1.0
    json_path, csv_path = export_evaluation_run(store, run_id, tmp_path / "exports")
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["privacy"].endswith("No audio.")
    assert report["export_manifest"]["product_id"] == "echo-link"
    assert report["export_manifest"]["redaction_applied"] is True
    assert report["export_manifest"]["contains_raw_audio"] is False
    assert "PRIVATE_EVALUATION_TOKEN" not in json_path.read_text(encoding="utf-8")
    assert "PRIVATE_EVALUATION_TOKEN" not in csv_path.read_text(encoding="utf-8")
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert rows[-1]["accepted_without_edit"] == "1"


def test_active_evaluation_run_can_resume_and_finish(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    run_id = store.start_evaluation_run("final")
    assert store.get_active_evaluation_run()["id"] == run_id
    trial_id = store.start_evaluation_trial(run_id, "prose-01")
    assert store.list_evaluation_trials(run_id)[0]["decision"] == "recording"
    store.complete_evaluation_trial(trial_id, "rejected", notes="No clear speech")
    store.finish_evaluation_run(run_id)
    assert store.get_active_evaluation_run() is None
    assert store.get_evaluation_run(run_id)["status"] == "complete"
