from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .correction import _protect_text, deterministic_cleanup
from .developer_text import format_developer_text
from .selected_text import COMMANDS, PROFILES, _CODE_IDENTIFIER, _CODE_SPAN


CONTRACT_VERSION = "1.0.0"
CLIENT_PLATFORMS = ("windows", "swift", "android")
OPERATIONS = (
    "capabilities",
    "dictation_cleanup",
    "developer_format",
    "protected_tokens",
    "writing_command",
)
ERROR_STAGES = (
    "request_validation",
    "capability_negotiation",
    "microphone_capture",
    "speech_transcription",
    "input_protection",
    "dictation_cleanup",
    "developer_formatting",
    "writing_model",
    "output_validation",
    "text_insertion",
    "local_storage",
    "canceled",
)
SAFE_PRIVACY_FLAGS: dict[str, object] = {
    "local_only": True,
    "external_network_allowed": False,
    "loopback_model_allowed": False,
    "retain_audio": False,
    "text_retention": "none",
    "telemetry_allowed": False,
}


@dataclass(frozen=True, slots=True)
class ContractFault(Exception):
    code: str
    stage: str
    message: str
    retryable: bool = False


def run_contract_request(request: Mapping[str, Any]) -> dict[str, Any]:
    """Run a local deterministic contract request or prepare a local writing request."""

    request_id = (
        _safe_identifier(request.get("request_id"))
        if isinstance(request, Mapping)
        else "invalid"
    )
    operation = (
        _safe_identifier(request.get("operation"))
        if isinstance(request, Mapping)
        else "unknown"
    )
    try:
        normalized = validate_contract_request(request)
        request_id = normalized["request_id"]
        operation = normalized["operation"]
        privacy = normalized["privacy"]
        payload = normalized["input"]
        if operation == "capabilities":
            output = _negotiate_capabilities(payload)
        elif operation == "dictation_cleanup":
            output = _run_dictation_cleanup(payload)
        elif operation == "developer_format":
            output = _run_developer_format(payload)
        elif operation == "protected_tokens":
            output = _run_protected_tokens(payload)
        else:
            output = _prepare_writing_command(payload)
        result = {
            "contract_version": CONTRACT_VERSION,
            "request_id": request_id,
            "operation": operation,
            "status": "ok",
            "privacy": privacy,
            "output": output,
        }
        validate_contract_result(result)
        return result
    except ContractFault as fault:
        return _error_result(request_id, operation, fault)
    except Exception:
        stage = {
            "dictation_cleanup": "dictation_cleanup",
            "developer_format": "developer_formatting",
            "protected_tokens": "input_protection",
            "writing_command": "input_protection",
            "capabilities": "capability_negotiation",
        }.get(operation, "request_validation")
        return _error_result(
            request_id,
            operation,
            ContractFault(
                "CORE_PROCESSING_FAILED",
                stage,
                "The local core could not complete the request.",
            ),
        )


def validate_contract_request(request: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(request, Mapping):
        raise ContractFault(
            "INVALID_REQUEST",
            "request_validation",
            "Request must be an object.",
        )
    _require_exact_keys(
        request,
        {"contract_version", "request_id", "operation", "privacy", "input"},
        "request",
    )
    if request["contract_version"] != CONTRACT_VERSION:
        raise ContractFault(
            "UNSUPPORTED_CONTRACT_VERSION",
            "capability_negotiation",
            "Contract version is not supported.",
        )
    request_id = _required_text(request["request_id"], "request_id", maximum=128)
    operation = _required_text(request["operation"], "operation", maximum=64)
    if operation not in OPERATIONS:
        raise ContractFault(
            "UNSUPPORTED_OPERATION",
            "request_validation",
            "Operation is not supported.",
        )
    privacy = _validate_privacy(request["privacy"])
    payload = request["input"]
    if not isinstance(payload, Mapping):
        raise ContractFault(
            "INVALID_INPUT",
            "request_validation",
            "Input must be an object.",
        )
    validators = {
        "capabilities": _validate_capability_input,
        "dictation_cleanup": _validate_dictation_input,
        "developer_format": _validate_developer_input,
        "protected_tokens": _validate_protected_input,
        "writing_command": _validate_writing_input,
    }
    normalized_input = validators[operation](payload)
    return {
        "contract_version": CONTRACT_VERSION,
        "request_id": request_id,
        "operation": operation,
        "privacy": privacy,
        "input": normalized_input,
    }


def validate_contract_result(result: Mapping[str, Any]) -> None:
    if not isinstance(result, Mapping):
        raise ContractFault(
            "INVALID_RESULT",
            "output_validation",
            "Result must be an object.",
        )
    base = {"contract_version", "request_id", "operation", "status", "privacy"}
    if result.get("status") == "ok":
        _require_exact_keys(result, base | {"output"}, "result")
        if not isinstance(result["output"], Mapping):
            raise ContractFault(
                "INVALID_RESULT",
                "output_validation",
                "Successful result output must be an object.",
            )
        operation = result.get("operation")
        if operation not in OPERATIONS:
            raise ContractFault(
                "INVALID_RESULT",
                "output_validation",
                "Successful result operation is invalid.",
            )
        output_validators = {
            "capabilities": _validate_capability_output,
            "dictation_cleanup": lambda output: _validate_text_output(
                output, "deterministic"
            ),
            "developer_format": lambda output: _validate_text_output(output, "developer"),
            "protected_tokens": _validate_protected_output,
            "writing_command": _validate_writing_output,
        }
        output_validators[operation](result["output"])
    elif result.get("status") == "error":
        _require_exact_keys(result, base | {"error"}, "result")
        _validate_error(result["error"])
    else:
        raise ContractFault(
            "INVALID_RESULT",
            "output_validation",
            "Result status must be ok or error.",
        )
    if result.get("contract_version") != CONTRACT_VERSION:
        raise ContractFault(
            "INVALID_RESULT",
            "output_validation",
            "Result contract version is invalid.",
        )
    _required_text(result.get("request_id"), "request_id", maximum=128)
    _required_text(result.get("operation"), "operation", maximum=64)
    _validate_privacy(result.get("privacy"))


def _validate_privacy(value: Any) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ContractFault(
            "INVALID_PRIVACY_FLAGS",
            "request_validation",
            "Privacy flags must be an object.",
        )
    _require_exact_keys(value, set(SAFE_PRIVACY_FLAGS), "privacy")
    if value.get("local_only") is not True:
        raise ContractFault(
            "LOCAL_ONLY_REQUIRED",
            "request_validation",
            "Local-only processing is required.",
        )
    for key in ("external_network_allowed", "retain_audio", "telemetry_allowed"):
        if value.get(key) is not False:
            raise ContractFault(
                "PRIVACY_DOWNGRADE_REJECTED",
                "request_validation",
                "The request weakens required privacy controls.",
            )
    if not isinstance(value.get("loopback_model_allowed"), bool):
        raise ContractFault(
            "INVALID_PRIVACY_FLAGS",
            "request_validation",
            "Loopback model permission must be true or false.",
        )
    if value.get("text_retention") not in ("none", "explicit_opt_in"):
        raise ContractFault(
            "INVALID_PRIVACY_FLAGS",
            "request_validation",
            "Text retention must be none or explicit_opt_in.",
        )
    return dict(value)


def _validate_capability_input(value: Mapping[str, Any]) -> dict[str, Any]:
    _require_exact_keys(
        value,
        {
            "client_platform",
            "client_version",
            "supported_contract_versions",
            "requested_operations",
        },
        "capability input",
    )
    platform = _required_text(value["client_platform"], "client_platform", maximum=32)
    if platform not in CLIENT_PLATFORMS:
        raise ContractFault(
            "UNSUPPORTED_CLIENT_PLATFORM",
            "capability_negotiation",
            "Client platform is not supported by this contract.",
        )
    versions = _text_list(value["supported_contract_versions"], "supported_contract_versions")
    operations = _text_list(value["requested_operations"], "requested_operations")
    return {
        "client_platform": platform,
        "client_version": _required_text(value["client_version"], "client_version", maximum=64),
        "supported_contract_versions": versions,
        "requested_operations": operations,
    }


def _validate_dictation_input(value: Mapping[str, Any]) -> dict[str, Any]:
    _require_exact_keys(value, {"text", "vocabulary", "append_trailing_space"}, "dictation input")
    return {
        "text": _input_text(value["text"]),
        "vocabulary": _text_map(value["vocabulary"], "vocabulary"),
        "append_trailing_space": _required_bool(
            value["append_trailing_space"], "append_trailing_space"
        ),
    }


def _validate_developer_input(value: Mapping[str, Any]) -> dict[str, Any]:
    _require_exact_keys(value, {"text", "vocabulary", "append_trailing_space"}, "developer input")
    return {
        "text": _input_text(value["text"]),
        "vocabulary": _text_map(value["vocabulary"], "vocabulary"),
        "append_trailing_space": _required_bool(
            value["append_trailing_space"], "append_trailing_space"
        ),
    }


def _validate_protected_input(value: Mapping[str, Any]) -> dict[str, Any]:
    _require_exact_keys(value, {"text", "protected_terms"}, "protected-token input")
    return {
        "text": _input_text(value["text"]),
        "protected_terms": _text_list(value["protected_terms"], "protected_terms"),
    }


def _validate_writing_input(value: Mapping[str, Any]) -> dict[str, Any]:
    _require_exact_keys(
        value,
        {"text", "command", "profile", "instruction", "language", "model", "protected_terms"},
        "writing input",
    )
    command = _required_text(value["command"], "command", maximum=32)
    profile = _required_text(value["profile"], "profile", maximum=32)
    if command not in COMMANDS:
        raise ContractFault(
            "UNKNOWN_WRITING_COMMAND",
            "request_validation",
            "Writing command is not supported.",
        )
    if profile not in PROFILES:
        raise ContractFault(
            "UNKNOWN_WRITING_PROFILE",
            "request_validation",
            "Writing profile is not supported.",
        )
    language = _optional_text(value["language"], "language", maximum=80)
    if command == "Translate" and not language:
        raise ContractFault(
            "TRANSLATION_LANGUAGE_REQUIRED",
            "request_validation",
            "Translation language is required.",
        )
    return {
        "text": _input_text(value["text"]),
        "command": command,
        "profile": profile,
        "instruction": _optional_text(value["instruction"], "instruction", maximum=2000),
        "language": language,
        "model": _required_text(value["model"], "model", maximum=128),
        "protected_terms": _text_list(value["protected_terms"], "protected_terms"),
    }


def _run_dictation_cleanup(value: Mapping[str, Any]) -> dict[str, Any]:
    text = deterministic_cleanup(value["text"], value["vocabulary"])
    if value["append_trailing_space"] and text and not text.endswith(" "):
        text += " "
    return {
        "text": text,
        "source": "deterministic",
        "changed": text.rstrip() != value["text"].rstrip(),
    }


def _run_developer_format(value: Mapping[str, Any]) -> dict[str, Any]:
    text = format_developer_text(value["text"], value["vocabulary"])
    if value["append_trailing_space"] and text and not text.endswith(" "):
        text += " "
    return {"text": text, "source": "developer", "changed": text.rstrip() != value["text"].rstrip()}


def _run_protected_tokens(value: Mapping[str, Any]) -> dict[str, Any]:
    protected, replacements = _protect_text(value["text"], value["protected_terms"])
    return {
        "protected_text": protected,
        "tokens": [
            {"token": token, "value": original}
            for token, original in replacements.items()
        ],
    }


def _prepare_writing_command(value: Mapping[str, Any]) -> dict[str, Any]:
    protected_terms = list(value["protected_terms"])
    if value["profile"] == "Code-safe":
        protected_terms.extend(match.group(0) for match in _CODE_SPAN.finditer(value["text"]))
        protected_terms.extend(match.group(0) for match in _CODE_IDENTIFIER.finditer(value["text"]))
    protected, replacements = _protect_text(value["text"], protected_terms)
    return {
        "phase": "prepared",
        "command": value["command"],
        "profile": value["profile"],
        "instruction": value["instruction"],
        "language": value["language"],
        "model": value["model"],
        "requires_local_model": True,
        "protected_text": protected,
        "tokens": [
            {"token": token, "value": original}
            for token, original in replacements.items()
        ],
    }


def _negotiate_capabilities(value: Mapping[str, Any]) -> dict[str, Any]:
    if CONTRACT_VERSION not in value["supported_contract_versions"]:
        raise ContractFault(
            "NO_COMPATIBLE_CONTRACT_VERSION",
            "capability_negotiation",
            "No compatible contract version was offered.",
        )
    unsupported = sorted(set(value["requested_operations"]) - set(OPERATIONS))
    if unsupported:
        raise ContractFault(
            "UNSUPPORTED_OPERATION",
            "capability_negotiation",
            "A requested operation is not supported.",
        )
    return {
        "selected_contract_version": CONTRACT_VERSION,
        "client_platform": value["client_platform"],
        "operations": {
            "dictation_cleanup": {"deterministic": True, "requires_local_model": False},
            "developer_format": {"deterministic": True, "requires_local_model": False},
            "protected_tokens": {"deterministic": True, "requires_local_model": False},
            "writing_command": {
                "deterministic_preparation": True,
                "completed_output_deterministic": False,
                "requires_local_model": True,
            },
        },
        "writing_commands": list(COMMANDS),
        "writing_profiles": list(PROFILES),
        "error_stages": list(ERROR_STAGES),
    }


def _error_result(request_id: str, operation: str, fault: ContractFault) -> dict[str, Any]:
    result = {
        "contract_version": CONTRACT_VERSION,
        "request_id": request_id or "invalid",
        "operation": operation or "unknown",
        "status": "error",
        "privacy": dict(SAFE_PRIVACY_FLAGS),
        "error": {
            "code": fault.code,
            "stage": fault.stage,
            "message": fault.message,
            "retryable": fault.retryable,
        },
    }
    validate_contract_result(result)
    return result


def _validate_error(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise ContractFault("INVALID_RESULT", "output_validation", "Error must be an object.")
    _require_exact_keys(value, {"code", "stage", "message", "retryable"}, "error")
    _required_text(value["code"], "error code", maximum=64)
    _required_text(value["message"], "error message", maximum=500)
    if value["stage"] not in ERROR_STAGES:
        raise ContractFault("INVALID_RESULT", "output_validation", "Error stage is invalid.")
    _required_bool(value["retryable"], "retryable")


def _validate_text_output(value: Mapping[str, Any], source: str) -> None:
    _require_exact_keys(value, {"text", "source", "changed"}, "text output")
    if not isinstance(value["text"], str) or value["source"] != source:
        raise ContractFault("INVALID_RESULT", "output_validation", "Text output is invalid.")
    _required_bool(value["changed"], "changed")


def _validate_protected_output(value: Mapping[str, Any]) -> None:
    _require_exact_keys(value, {"protected_text", "tokens"}, "protected output")
    if not isinstance(value["protected_text"], str):
        raise ContractFault(
            "INVALID_RESULT", "output_validation", "Protected text is invalid."
        )
    _validate_tokens(value["tokens"])


def _validate_writing_output(value: Mapping[str, Any]) -> None:
    phase = value.get("phase")
    if phase == "prepared":
        _require_exact_keys(
            value,
            {
                "phase",
                "command",
                "profile",
                "instruction",
                "language",
                "model",
                "requires_local_model",
                "protected_text",
                "tokens",
            },
            "prepared writing output",
        )
        if value["command"] not in COMMANDS or value["profile"] not in PROFILES:
            raise ContractFault(
                "INVALID_RESULT", "output_validation", "Writing output is invalid."
            )
        for key in ("instruction", "language", "model", "protected_text"):
            if not isinstance(value[key], str):
                raise ContractFault(
                    "INVALID_RESULT", "output_validation", "Writing output is invalid."
                )
        if value["requires_local_model"] is not True:
            raise ContractFault(
                "INVALID_RESULT", "output_validation", "Writing output is invalid."
            )
        _validate_tokens(value["tokens"])
        return
    if phase == "completed":
        _require_exact_keys(
            value,
            {
                "phase",
                "text",
                "source",
                "model",
                "command",
                "profile",
                "protected_tokens_preserved",
            },
            "completed writing output",
        )
        if (
            not isinstance(value["text"], str)
            or value["source"] != "local_model"
            or value["command"] not in COMMANDS
            or value["profile"] not in PROFILES
        ):
            raise ContractFault(
                "INVALID_RESULT", "output_validation", "Writing output is invalid."
            )
        _required_text(value["model"], "model", maximum=128)
        _required_bool(value["protected_tokens_preserved"], "protected_tokens_preserved")
        return
    raise ContractFault(
        "INVALID_RESULT", "output_validation", "Writing output phase is invalid."
    )


def _validate_capability_output(value: Mapping[str, Any]) -> None:
    _require_exact_keys(
        value,
        {
            "selected_contract_version",
            "client_platform",
            "operations",
            "writing_commands",
            "writing_profiles",
            "error_stages",
        },
        "capability output",
    )
    if (
        value["selected_contract_version"] != CONTRACT_VERSION
        or value["client_platform"] not in CLIENT_PLATFORMS
        or not isinstance(value["operations"], Mapping)
        or list(value["writing_commands"]) != list(COMMANDS)
        or list(value["writing_profiles"]) != list(PROFILES)
        or list(value["error_stages"]) != list(ERROR_STAGES)
    ):
        raise ContractFault(
            "INVALID_RESULT", "output_validation", "Capability output is invalid."
        )


def _validate_tokens(value: Any) -> None:
    if not isinstance(value, list):
        raise ContractFault("INVALID_RESULT", "output_validation", "Tokens are invalid.")
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise ContractFault("INVALID_RESULT", "output_validation", "Tokens are invalid.")
        _require_exact_keys(item, {"token", "value"}, "token")
        token = _required_text(item["token"], "token", maximum=64)
        _required_text(item["value"], "token value", maximum=200_000)
        number = token.removeprefix("ECHOLINKTOKEN").removesuffix("X")
        if (
            not token.startswith("ECHOLINKTOKEN")
            or not token.endswith("X")
            or not number.isdigit()
            or token in seen
        ):
            raise ContractFault("INVALID_RESULT", "output_validation", "Tokens are invalid.")
        seen.add(token)


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ContractFault(
            "INVALID_FIELDS",
            "request_validation",
            f"{label.capitalize()} fields are invalid.",
        )


def _required_text(value: Any, label: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ContractFault(
            "INVALID_TEXT_FIELD",
            "request_validation",
            f"{label.capitalize()} is invalid.",
        )
    return value.strip()


def _optional_text(value: Any, label: str, *, maximum: int) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise ContractFault(
            "INVALID_TEXT_FIELD",
            "request_validation",
            f"{label.capitalize()} is invalid.",
        )
    return value.strip()


def _input_text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200_000:
        raise ContractFault(
            "INVALID_INPUT_TEXT",
            "request_validation",
            "Input text is empty or too large.",
        )
    return value


def _required_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ContractFault(
            "INVALID_BOOLEAN_FIELD",
            "request_validation",
            f"{label.capitalize()} must be true or false.",
        )
    return value


def _text_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or len(value) > 256:
        raise ContractFault(
            "INVALID_TEXT_LIST",
            "request_validation",
            f"{label.capitalize()} must be a bounded text list.",
        )
    normalized: list[str] = []
    for item in value:
        normalized.append(_required_text(item, label, maximum=500))
    if len(normalized) != len(set(normalized)):
        raise ContractFault(
            "DUPLICATE_LIST_VALUE",
            "request_validation",
            f"{label.capitalize()} contains duplicates.",
        )
    return normalized


def _text_map(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or len(value) > 1000:
        raise ContractFault(
            "INVALID_TEXT_MAP",
            "request_validation",
            f"{label.capitalize()} must be a bounded text map.",
        )
    result: dict[str, str] = {}
    for key, replacement in value.items():
        spoken = _required_text(key, label, maximum=200)
        result[spoken] = _required_text(replacement, label, maximum=500)
    return result


def _safe_identifier(value: Any) -> str:
    if isinstance(value, str) and 0 < len(value) <= 128:
        return value
    return "invalid"
