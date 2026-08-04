from __future__ import annotations

import importlib.util
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANUSCRIPT_ROOT = (
    PROJECT_ROOT / "Manuscript"
    if (PROJECT_ROOT / "Manuscript").is_dir()
    else PROJECT_ROOT.parent
)
VALIDATOR_PATH = MANUSCRIPT_ROOT / "iOS" / "validate_foundation.py"
SPEC = importlib.util.spec_from_file_location("apple_foundation_validator", VALIDATOR_PATH)
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def test_apple_foundation_and_swift_contract_source_match_authoritative_v1() -> None:
    report = VALIDATOR.validate_foundation()

    assert report == {
        "plists": 7,
        "swift_files": 9,
        "contract_cases": 10,
        "contract_schemas": 4,
        "cleanup_goldens": 2,
        "cleanup_regressions": 14,
    }
