# Local writing models

Echo-Link uses writing models only through the loopback Ollama service. It does not download a model, contact a model registry, or fall back to a hosted endpoint.

## Model manager

Open **Settings > Local Models > Manage writing models**.

- **Refresh** lists only models already installed in the local Ollama store.
- Each row shows the model name and tag, size, Ollama digest, family, and quantization.
- **Import local GGUF** accepts one user-chosen `.gguf` file. Echo-Link rejects symbolic links and invalid GGUF headers, calculates the source SHA256, and calls the local `ollama create` workflow.
- **Run local speed check** requests a bounded 16-token response, unloads the model afterward, and reports load time and output speed.
- **Use selected** changes the writing model only after Settings is saved.
- **Remove selected** requires confirmation and is disabled for the currently configured model.

Ollama documents the same local GGUF `FROM /path/to/file.gguf` import flow in its [official import guide](https://docs.ollama.com/import).

## Privacy and safety rules

- Endpoint must be `http://127.0.0.1`, `http://localhost`, or `http://[::1]` with an optional port.
- Import never follows symbolic links.
- Model names are validated before they reach the local command line.
- No raw dictation or personal document is used by the speed check.
- The benchmark sets `keep_alive` to zero so the local model can unload after the check.
- Removal never runs without a user confirmation.

## Acceptance

Automated tests prove loopback enforcement, exact tag/digest parsing, GGUF validation, source hashing, command argument safety, bounded benchmark behavior, destructive-action confirmation wiring, and model selection. The current release machine passed `tools/writing_model_probe.py`; its safe metadata-only record is `docs/qa/writing-model-evidence.json`. Repeat this live check on each release machine.
