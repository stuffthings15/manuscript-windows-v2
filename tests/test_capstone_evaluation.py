from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "evaluate_capstone.py"
SPEC = importlib.util.spec_from_file_location("evaluate_capstone", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_gold_corpus_is_fixed_and_unique():
    payload = MODULE.load_corpus(MODULE.DEFAULT_CORPUS)
    identifiers = [case["id"] for case in payload["cases"]]
    assert len(identifiers) == 20
    assert len(identifiers) == len(set(identifiers))
    assert MODULE.DEFAULT_OUTPUT.name == "current_results.json"
    verify_script = (ROOT / "scripts" / "verify.ps1").read_text(encoding="utf-8")
    assert "WriteCurrent" in verify_script
    assert "WriteBaseline" not in verify_script


def test_evaluation_records_current_role_and_unmeasured_human_metrics():
    payload = {
        "schema_version": 1,
        "name": "test",
        "cases": [
            {
                "id": "one",
                "category": "prose",
                "raw": "hello hello world",
                "expected": "Hello world.",
                "protected_terms": [],
            }
        ],
    }
    report = MODULE.evaluate_corpus(payload, repeats=2)
    assert report["schema_version"] == 2
    assert report["run_role"] == "current"
    assert report["automated_result"]["exact_match_rate"] == 1.0
    assert report["human_metrics"]["correction_free_acceptance_rate"] is None
    assert report["human_metrics"]["median_time_to_accepted_text_seconds"] is None


def test_locked_baseline_requires_explicit_overwrite_flag():
    before = MODULE.BASELINE_OUTPUT.read_bytes()
    result = MODULE.main(
        [
            "--output",
            str(MODULE.BASELINE_OUTPUT),
            "--repeats",
            "1",
        ]
    )
    assert result == 2
    assert MODULE.BASELINE_OUTPUT.read_bytes() == before


def test_baseline_replacement_also_requires_baseline_role():
    before = MODULE.BASELINE_OUTPUT.read_bytes()
    result = MODULE.main(
        [
            "--output",
            str(MODULE.BASELINE_OUTPUT),
            "--replace-baseline",
            "--repeats",
            "1",
        ]
    )
    assert result == 2
    assert MODULE.BASELINE_OUTPUT.read_bytes() == before


def test_locked_baseline_keeps_original_before_metrics():
    payload = __import__("json").loads(MODULE.BASELINE_OUTPUT.read_text(encoding="utf-8"))
    metrics = payload["automated_baseline"]
    assert metrics["exact_match_count"] == 11
    assert metrics["protected_content_pass_count"] == 14


def test_current_result_records_commit_and_corpus_hash(tmp_path):
    output = tmp_path / "current.json"
    assert MODULE.main(["--output", str(output), "--repeats", "1"]) == 0
    payload = __import__("json").loads(output.read_text(encoding="utf-8"))
    assert payload["run_role"] == "current"
    assert "automated_baseline" not in payload
    assert len(payload["evidence"]["corpus_sha256"]) == 64
    assert payload["evidence"]["code_commit"]
