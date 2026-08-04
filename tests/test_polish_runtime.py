from __future__ import annotations

import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from echo_link_core import polish_runtime
from echo_link_core.polish_runtime import (
    AdaptiveInferenceRuntime,
    NamedPipeLlamaRuntime,
    OllamaInferenceRuntime,
)
from echo_link_core.polish_runtime_packs import RuntimePackError
from echo_link_core.polish_types import (
    ExecutionPlan,
    GenerationRequest,
    PolishMode,
    PolishTask,
    RuntimeBackend,
)


def plan() -> ExecutionPlan:
    return ExecutionPlan(
        policy_version=1,
        mode=PolishMode.BALANCED,
        task=PolishTask.PROOFREAD,
        use_llm=True,
        pack_id="qwen3-14b-q4-k-m-v1",
        model="qwen3:14b",
        backend=RuntimeBackend.CUDA,
        context_window=2048,
        max_output_tokens=128,
        warm_latency_limit_seconds=5.0,
        require_full_gpu=True,
    )


def test_pipe_client_rejects_non_manuscript_pipe_names():
    with pytest.raises(ValueError, match="not Manuscript-owned"):
        NamedPipeLlamaRuntime(r"\\.\pipe\OtherProduct")


def test_pipe_protocol_always_adds_version_and_random_auth(monkeypatch):
    response = json.dumps({"ok": True, "runtime": "llama.cpp"}).encode()
    incoming = bytearray(struct.pack("<I", len(response)) + response)
    written = bytearray()

    class FakePipe:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def write(self, value):
            written.extend(value)
            return len(value)

        def read(self, size):
            value = bytes(incoming[:size])
            del incoming[:size]
            return value

    class FakePath:
        def __init__(self, _value):
            pass

        def open(self, *_args, **_kwargs):
            return FakePipe()

    runtime = NamedPipeLlamaRuntime(
        r"\\.\pipe\ManuscriptPolish-0123456789abcdef",
        secret="a" * 64,
    )
    monkeypatch.setattr(polish_runtime, "Path", FakePath)
    monkeypatch.setattr(polish_runtime, "_wait_named_pipe", lambda *_args: None)
    value = runtime._exchange({"type": "hello"}, timeout=1.0)
    assert value["ok"]
    size = struct.unpack("<I", written[:4])[0]
    request = json.loads(written[4 : 4 + size])
    assert request["protocol_version"] == 1
    assert request["auth"] == "a" * 64


def test_adaptive_runtime_prepares_verified_host_and_backend(
    tmp_path,
    monkeypatch,
):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUFmodel")
    server = tmp_path / "runtime" / "llama-server.exe"
    server.parent.mkdir()
    server.write_bytes(b"MZ")
    host = tmp_path / "Manuscript.PolishHost.exe"
    host.write_bytes(b"MZ")
    captured = {}

    class FakeModelPacks:
        def model_file(self, pack_id):
            assert pack_id == plan().pack_id
            return model

    class FakeRuntimePacks:
        def ensure(self, backend, *, consent):
            assert backend is RuntimeBackend.CUDA
            assert consent is True
            return SimpleNamespace(executable_path=server)

        def probe(self, pack):
            return pack

    class FakePipeRuntime:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def hello(self, *, timeout):
            return {"ok": True}

        def close(self):
            pass

    monkeypatch.setattr(polish_runtime, "NamedPipeLlamaRuntime", FakePipeRuntime)
    value = AdaptiveInferenceRuntime(
        model_packs=FakeModelPacks(),
        runtime_packs=FakeRuntimePacks(),
        host_path=host,
    )
    assert value.prepare(plan(), consent=True)
    assert captured["server_path"] == server
    assert captured["model_path"] == model
    assert captured["backend"] is RuntimeBackend.CUDA


def test_adaptive_runtime_keeps_ollama_rollback_on_runtime_failure(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUFmodel")

    class FakeModelPacks:
        def model_file(self, _pack_id):
            return model

    class FailedRuntimePacks:
        def ensure(self, _backend, *, consent):
            raise RuntimePackError("measured CUDA probe failed")

    value = AdaptiveInferenceRuntime(
        model_packs=FakeModelPacks(),
        runtime_packs=FailedRuntimePacks(),
        host_path=tmp_path / "missing.exe",
    )
    assert not value.prepare(plan(), consent=True)
    assert "measured CUDA probe failed" in value.last_downgrade_reason


def test_ollama_acceptance_probe_separates_cold_load_from_warm_latency():
    calls = []

    class FakeAssistant:
        def require_gpu(self, model, **kwargs):
            calls.append(("load", model, kwargs["timeout"]))

        def run(self, text, **kwargs):
            calls.append(("generate", text, kwargs["timeout"]))
            return SimpleNamespace(
                text=(
                    "The report is not ready, and it failed. Keep build 47 at "
                    "http://127.0.0.1:7777/protected until July 23, 2026."
                ),
                model=kwargs["model"],
            )

        def runtime_status(self, model, *, timeout):
            return SimpleNamespace(
                model=model,
                loaded=True,
                backend="GPU",
                model_bytes=9,
                gpu_bytes=9,
            )

    value = OllamaInferenceRuntime(assistant=FakeAssistant())
    result = value.acceptance_probe(plan())
    assert result.accepted
    assert calls[0] == ("load", "qwen3:14b", 90.0)
    assert [call[0] for call in calls[1:]] == ["generate", "generate", "generate"]
    assert all(call[2] == 30.0 for call in calls[1:])
    assert all(
        call[1].startswith("The report are not ready, and it fail.")
        for call in calls[1:]
    )


def test_ollama_generation_defers_surface_guard_to_polish_validator():
    calls = []

    class FakeAssistant:
        def run(self, text, **kwargs):
            calls.append((text, kwargs))
            return SimpleNamespace(text="Corrected text.", model=kwargs["model"])

        def runtime_status(self, model, *, timeout):
            return SimpleNamespace(
                model=model,
                loaded=True,
                backend="GPU",
                model_bytes=9,
                gpu_bytes=9,
            )

    runtime = OllamaInferenceRuntime(assistant=FakeAssistant())
    result = runtime.generate(
        GenerationRequest(
            text="Bad text.",
            command="Rephrase",
            profile="General",
            instruction="",
            language="",
            protected_terms=(),
            plan=plan(),
        )
    )

    assert result.text == "Corrected text."
    assert calls[0][1]["defer_meaning_validation"] is True
