from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "evaluate_writing_quality.py"
SPEC = importlib.util.spec_from_file_location("evaluate_writing_quality", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_reference_self_check_writes_deterministic_offline_report(tmp_path: Path):
    output = tmp_path / "report.json"
    assert MODULE.main(["--reference-self-check", "--check", "--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["summary"]["all_passed"] is True
    assert report["offline"] is True
    assert report["limitations"]


def test_response_file_failure_returns_nonzero_with_check(tmp_path: Path):
    responses = tmp_path / "responses.json"
    responses.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "responses": [{"id": "grammar-01", "text": "Still wrong"}],
            }
        ),
        encoding="utf-8",
    )
    assert MODULE.main(["--responses", str(responses), "--check", "--output", str(tmp_path / "out.json")]) == 1


def test_response_file_rejects_duplicate_ids(tmp_path: Path):
    responses = tmp_path / "responses.json"
    responses.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "responses": [
                    {"id": "grammar-01", "text": "one"},
                    {"id": "grammar-01", "text": "two"},
                ],
            }
        ),
        encoding="utf-8",
    )
    assert MODULE.main(["--responses", str(responses)]) == 2
