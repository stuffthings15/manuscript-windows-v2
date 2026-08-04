# Echo-Link Local Core Contract

Current frozen version: `v1` / `1.0.0`.

This folder defines one UTF-8 JSON contract for Windows, Swift, and Android clients. It locks the portable behavior shared by those clients. It does not claim that native clients, device integrations, installers, or app-store packages exist.

## What is locked

- deterministic dictation cleanup;
- deterministic developer formatting;
- protected-token extraction and token ordering;
- local writing-command preparation and completed-result shape;
- capability negotiation;
- stable error stages;
- fail-closed local-only privacy flags.

The schemas are in `v1/schemas`. Exact fixtures are in `v1/fixtures/golden.json`. The current Python reference adapter is `src/echo_link_core/shared_contract.py`.

## Request flow

Every request contains:

1. `contract_version`;
2. a caller-generated `request_id`;
3. one supported `operation`;
4. all privacy flags;
5. an operation-specific `input` object.

Every result repeats the version, request ID, operation, and effective privacy flags. A result contains either `status: ok` with `output`, or `status: error` with a stable error object. Unknown fields are rejected.

Clients must negotiate `capabilities` before depending on an operation. A successful negotiation means the wire contract is compatible. It is not proof that the native client implemented or device-tested the feature.

## Privacy rules

The contract requires:

- `local_only: true`;
- `external_network_allowed: false`;
- `retain_audio: false`;
- `telemetry_allowed: false`;
- text retention set to `none` or explicit opt-in only.

`loopback_model_allowed` permits a local model bridge such as Ollama on loopback. It never permits a hosted endpoint. Invalid or weaker privacy flags fail closed, and error results return the safest privacy set.

The reference adapter performs no file writes, network calls, process launches, audio work, or insertion. Writing-command preparation protects inputs and validates the command. It does not run a model. Completed local-model prose uses the result schema but is intentionally excluded from exact-output goldens.

## Native client mapping

- Windows: parse objects with the platform JSON library and preserve UTF-8 text and field names exactly.
- Swift: model requests and results with `Codable`; use string-backed enums and reject unknown required enum values.
- Android: model requests and results with Kotlin serialization or an equivalent strict JSON decoder; keep unknown keys disabled for conformance runs.

All clients must keep token strings such as `ECHOLINKTOKEN0X` byte-for-byte unchanged. Array order is significant for protected tokens, commands, profiles, and error stages.

## Versioning

- Patch: clarification or fixture expansion with no changed existing output.
- Minor: additive optional capability that old clients can ignore.
- Major: removed or renamed field, changed enum meaning, changed privacy rule, or changed existing golden output.

Breaking changes go in a new major folder. Do not rewrite `v1` goldens to hide drift.

## Conformance

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_shared_contract.py -q
```

The test suite loads every schema and manifest path, rejects remote `$ref` values, runs every golden request through the current Python core, compares exact output, checks all three client identifiers, covers every writing command/profile, rejects privacy downgrades, and confirms the adapter imports no network, process, or storage module.
