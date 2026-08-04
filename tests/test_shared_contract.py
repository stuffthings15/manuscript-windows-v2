from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pytest

from echo_link_core.selected_text import COMMANDS, PROFILES
from echo_link_core.shared_contract import (
    CLIENT_PLATFORMS,
    CONTRACT_VERSION,
    ERROR_STAGES,
    OPERATIONS,
    SAFE_PRIVACY_FLAGS,
    ContractFault,
    run_contract_request,
    validate_contract_request,
    validate_contract_result,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = ROOT / "shared" / "contract" / "v1"
FIXTURE_PATH = CONTRACT_ROOT / "fixtures" / "golden.json"


def _fixture_payload():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _request(case, payload):
    privacy = dict(payload["default_privacy"])
    privacy.update(case.get("privacy_overrides") or {})
    return {
        "contract_version": payload["contract_version"],
        "request_id": case["id"],
        "operation": case["operation"],
        "privacy": privacy,
        "input": case["input"],
    }


def _basic_request(operation, input_value, *, platform_privacy=None):
    privacy = dict(SAFE_PRIVACY_FLAGS)
    privacy.update(platform_privacy or {})
    return {
        "contract_version": CONTRACT_VERSION,
        "request_id": f"test-{operation}",
        "operation": operation,
        "privacy": privacy,
        "input": input_value,
    }


def test_manifest_is_versioned_and_all_local_files_exist():
    manifest = json.loads((CONTRACT_ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["contract_version"] == CONTRACT_VERSION
    assert manifest["client_targets"] == list(CLIENT_PLATFORMS)
    assert manifest["operations"] == list(OPERATIONS)
    assert "does not prove" in manifest["limitations"][0]
    for relative in (*manifest["schemas"].values(), manifest["golden_fixtures"]):
        path = CONTRACT_ROOT / relative
        assert path.is_file()
        assert path.resolve().is_relative_to(CONTRACT_ROOT.resolve())


def test_json_schemas_use_v1_ids_strict_objects_and_only_local_refs():
    schema_paths = sorted((CONTRACT_ROOT / "schemas").glob("*.schema.json"))
    assert [path.name for path in schema_paths] == [
        "contract-request.schema.json",
        "contract-result.schema.json",
        "error.schema.json",
        "privacy-flags.schema.json",
    ]
    for path in schema_paths:
        schema = json.loads(path.read_text(encoding="utf-8"))
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"].startswith("urn:echo-link:contract:v1:")
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        for value in _walk_values(schema, "$ref"):
            assert not value.startswith(("http://", "https://"))


def test_json_schema_writing_commands_match_current_python_core_exactly():
    request_schema = json.loads(
        (CONTRACT_ROOT / "schemas" / "contract-request.schema.json").read_text(
            encoding="utf-8"
        )
    )
    result_schema = json.loads(
        (CONTRACT_ROOT / "schemas" / "contract-result.schema.json").read_text(
            encoding="utf-8"
        )
    )
    request_commands = request_schema["$defs"]["writingInput"]["properties"]["command"][
        "enum"
    ]
    result_commands = result_schema["$defs"]["capabilityOutput"]["properties"][
        "writing_commands"
    ]

    assert request_commands == list(COMMANDS)
    assert result_commands["minItems"] == len(COMMANDS)
    assert result_commands["maxItems"] == len(COMMANDS)
    assert result_commands["items"]["enum"] == list(COMMANDS)


def test_golden_fixtures_match_current_python_core_exactly():
    payload = _fixture_payload()
    assert payload["fixture_schema_version"] == 1
    assert payload["contract_version"] == CONTRACT_VERSION
    identifiers = [case["id"] for case in payload["cases"]]
    assert len(identifiers) == len(set(identifiers))
    assert {case["operation"] for case in payload["cases"]} == set(OPERATIONS)

    for case in payload["cases"]:
        request = _request(case, payload)
        result = run_contract_request(request)
        validate_contract_result(result)
        assert result["contract_version"] == CONTRACT_VERSION, case["id"]
        assert result["request_id"] == case["id"], case["id"]
        assert result["operation"] == case["operation"], case["id"]
        assert result["status"] == case["expected_status"], case["id"]
        if result["status"] == "ok":
            assert result["privacy"] == request["privacy"], case["id"]
            assert result["output"] == case["expected_output"], case["id"]
        else:
            assert result["privacy"] == SAFE_PRIVACY_FLAGS, case["id"]
            assert result["error"] == case["expected_error"], case["id"]


@pytest.mark.parametrize("platform", CLIENT_PLATFORMS)
def test_capability_negotiation_covers_windows_swift_and_android(platform):
    request = _basic_request(
        "capabilities",
        {
            "client_platform": platform,
            "client_version": "0.1.0",
            "supported_contract_versions": [CONTRACT_VERSION],
            "requested_operations": list(OPERATIONS[1:]),
        },
    )
    result = run_contract_request(request)
    assert result["status"] == "ok"
    assert result["output"]["client_platform"] == platform
    assert result["output"]["selected_contract_version"] == CONTRACT_VERSION
    assert result["output"]["writing_commands"] == list(COMMANDS)
    assert result["output"]["writing_profiles"] == list(PROFILES)
    assert result["output"]["error_stages"] == list(ERROR_STAGES)
    assert "native_implementation" not in result["output"]


@pytest.mark.parametrize(
    ("flag", "unsafe_value", "expected_code"),
    (
        ("local_only", False, "LOCAL_ONLY_REQUIRED"),
        ("external_network_allowed", True, "PRIVACY_DOWNGRADE_REJECTED"),
        ("retain_audio", True, "PRIVACY_DOWNGRADE_REJECTED"),
        ("telemetry_allowed", True, "PRIVACY_DOWNGRADE_REJECTED"),
    ),
)
def test_privacy_downgrades_fail_closed(flag, unsafe_value, expected_code):
    request = _basic_request(
        "developer_format",
        {"text": "camel case user name", "vocabulary": {}, "append_trailing_space": False},
    )
    request["privacy"][flag] = unsafe_value
    result = run_contract_request(request)
    assert result["status"] == "error"
    assert result["error"]["code"] == expected_code
    assert result["privacy"] == SAFE_PRIVACY_FLAGS


def test_contract_rejects_unknown_fields_and_versions():
    request = _basic_request(
        "protected_tokens",
        {"text": "Echo-Link 3.12", "protected_terms": ["Echo-Link"]},
    )
    invalid_field = copy.deepcopy(request)
    invalid_field["cloud"] = True
    with pytest.raises(ContractFault):
        validate_contract_request(invalid_field)

    wrong_version = copy.deepcopy(request)
    wrong_version["contract_version"] = "2.0.0"
    result = run_contract_request(wrong_version)
    assert result["status"] == "error"
    assert result["error"]["code"] == "UNSUPPORTED_CONTRACT_VERSION"
    assert result["error"]["stage"] == "capability_negotiation"


@pytest.mark.parametrize("command", tuple(COMMANDS))
@pytest.mark.parametrize("profile", tuple(PROFILES))
def test_every_current_writing_command_and_profile_prepares_offline(command, profile):
    request = _basic_request(
        "writing_command",
        {
            "text": "Echo-Link must keep API_KEY at 3.",
            "command": command,
            "profile": profile,
            "instruction": "Keep the meaning.",
            "language": "French" if command == "Translate" else "",
            "model": "local-test-model",
            "protected_terms": ["Echo-Link", "API_KEY"],
        },
        platform_privacy={"loopback_model_allowed": True},
    )
    result = run_contract_request(request)
    assert result["status"] == "ok"
    assert result["output"]["phase"] == "prepared"
    assert result["output"]["requires_local_model"] is True
    assert {token["value"] for token in result["output"]["tokens"]} >= {
        "Echo-Link",
        "API_KEY",
        "3",
    }


def test_completed_writing_result_contract_is_validated_without_running_a_model():
    result = {
        "contract_version": CONTRACT_VERSION,
        "request_id": "completed-writing",
        "operation": "writing_command",
        "status": "ok",
        "privacy": dict(SAFE_PRIVACY_FLAGS),
        "output": {
            "phase": "completed",
            "text": "Finished local text.",
            "source": "local_model",
            "model": "local-test-model",
            "command": "Rewrite",
            "profile": "General",
            "protected_tokens_preserved": True,
        },
    }
    validate_contract_result(result)
    invalid = copy.deepcopy(result)
    invalid["output"]["source"] = "cloud"
    with pytest.raises(ContractFault):
        validate_contract_result(invalid)


def test_adapter_has_no_network_process_or_storage_imports():
    module_path = ROOT / "src" / "echo_link_core" / "shared_contract.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_roots.update(
        (node.module or "").split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )
    assert imported_roots.isdisjoint(
        {"socket", "subprocess", "urllib", "requests", "http", "sqlite3"}
    )


def _walk_values(value, key):
    if isinstance(value, dict):
        for item_key, item_value in value.items():
            if item_key == key and isinstance(item_value, str):
                yield item_value
            yield from _walk_values(item_value, key)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_values(item, key)
