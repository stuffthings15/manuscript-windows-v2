from __future__ import annotations

import json
import struct
import threading
from dataclasses import replace
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
    AcceptanceProbeResult,
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
        pack_id="gemma4-e4b-qat-v1",
        model="gemma4:e4b-it-qat",
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


def pipe_runtime(
    *,
    host_digest: str = "b" * 64,
    component_fingerprint: str = "c" * 64,
) -> NamedPipeLlamaRuntime:
    value = plan()
    return NamedPipeLlamaRuntime(
        r"\\.\pipe\ManuscriptPolish-0123456789abcdef",
        model_pack_id=value.pack_id,
        model_name=value.model,
        backend=value.backend,
        context_window=value.context_window,
        runtime_pack_id="llama-cuda-test",
        runtime_pack_version="1",
        host_executable_sha256=host_digest,
        runtime_component_fingerprint=component_fingerprint,
        secret="a" * 64,
    )


def fake_runtime_pack(server: Path, *, component_digest: str = "d" * 64):
    return SimpleNamespace(
        executable_path=server,
        pack_id="llama-cuda-test",
        version="1",
        components=(
            SimpleNamespace(
                filename="llama-server.exe",
                size_bytes=2,
                sha256=component_digest,
            ),
        ),
    )


def test_named_pipe_generation_rejects_cpu_spill_for_gpu_required_plan(
    monkeypatch,
):
    runtime = pipe_runtime()
    monkeypatch.setattr(
        runtime,
        "_exchange",
        lambda *_args, **_kwargs: {
            "ok": True,
            "text": "Dogs are good.",
            "model": plan().model,
            "backend": "cpu",
        },
    )

    with pytest.raises(RuntimeError, match="backend identity"):
        runtime.generate(_generation_request(plan()))


@pytest.mark.parametrize("reported_model", ["", "gemma4:e2b-it-qat"])
def test_named_pipe_generation_rejects_missing_or_wrong_model_identity(
    monkeypatch,
    reported_model,
):
    runtime = pipe_runtime()
    monkeypatch.setattr(
        runtime,
        "_exchange",
        lambda *_args, **_kwargs: {
            "ok": True,
            "text": "Dogs are good.",
            "model": reported_model,
            "backend": "cuda",
            "full_gpu_resident": True,
        },
    )

    with pytest.raises(RuntimeError, match="model identity"):
        runtime.generate(_generation_request(plan()))


@pytest.mark.parametrize("reported_model", ["", "gemma4:e2b-it-qat"])
def test_named_pipe_probe_rejects_missing_or_wrong_host_model_identity(
    monkeypatch,
    reported_model,
):
    runtime = pipe_runtime()
    monkeypatch.setattr(
        runtime,
        "_exchange",
        lambda *_args, **_kwargs: {
            "accepted": True,
            "elapsed_seconds": 0.5,
            "correctness_passed": True,
            "protected_passed": True,
            "backend": "cuda",
            "model": reported_model,
            "full_gpu_resident": True,
        },
    )

    result = runtime.acceptance_probe(plan())

    assert not result.accepted
    assert result.model_identity == reported_model
    assert "model identity" in result.reason.casefold()


@pytest.mark.parametrize("resident", [None, False])
def test_named_pipe_generation_requires_explicit_full_gpu_residency(
    monkeypatch,
    resident,
):
    runtime = pipe_runtime()
    payload = {
        "ok": True,
        "text": "Dogs are good.",
        "model": plan().model,
        "backend": plan().backend.value,
    }
    if resident is not None:
        payload["full_gpu_resident"] = resident
    monkeypatch.setattr(runtime, "_exchange", lambda *_args, **_kwargs: payload)

    with pytest.raises(RuntimeError, match="GPU"):
        runtime.generate(_generation_request(plan()))


@pytest.mark.parametrize("backend", [None, "vulkan", "rules"])
def test_named_pipe_generation_requires_exact_backend(monkeypatch, backend):
    runtime = pipe_runtime()
    payload = {
        "ok": True,
        "text": "Dogs are good.",
        "model": plan().model,
        "full_gpu_resident": True,
    }
    if backend is not None:
        payload["backend"] = backend
    monkeypatch.setattr(runtime, "_exchange", lambda *_args, **_kwargs: payload)

    with pytest.raises(RuntimeError, match="backend identity"):
        runtime.generate(_generation_request(plan()))


@pytest.mark.parametrize(
    ("field", "wrong"),
    (
        ("model", "gemma4:e2b-it-qat"),
        ("model_pack_id", "gemma4-e2b-qat-v1"),
        ("backend", "vulkan"),
        ("context_window", 4096),
    ),
)
def test_named_pipe_hello_rejects_wrong_runtime_identity(
    monkeypatch,
    field,
    wrong,
):
    runtime = pipe_runtime()
    payload = {
        "ok": True,
        "runtime": "llama.cpp",
        "host_version": "2.1.0",
        "model_pack_id": plan().pack_id,
        "model": plan().model,
        "backend": plan().backend.value,
        "context_window": plan().context_window,
    }
    payload[field] = wrong
    monkeypatch.setattr(runtime, "_exchange", lambda *_args, **_kwargs: payload)

    with pytest.raises(RuntimeError, match="identity"):
        runtime.hello()


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
            return fake_runtime_pack(server)

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
    assert captured["host_executable_sha256"] == polish_runtime._file_sha256(host)
    assert captured["runtime_component_fingerprint"] == (
        polish_runtime._runtime_component_fingerprint(fake_runtime_pack(server))
    )


def test_adaptive_runtime_replaces_dead_same_key_host(tmp_path, monkeypatch):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUFmodel")
    server = tmp_path / "runtime" / "llama-server.exe"
    server.parent.mkdir()
    server.write_bytes(b"MZ")
    host_path = tmp_path / "Manuscript.PolishHost.exe"
    host_path.write_bytes(b"MZ")

    class DeadHost:
        is_alive = False

        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    class FakeModelPacks:
        def model_file(self, _pack_id):
            return model

    class FakeRuntimePacks:
        def ensure(self, _backend, *, consent):
            assert consent
            return fake_runtime_pack(server)

        def probe(self, pack):
            return pack

    class ReplacementHost:
        is_alive = True
        runtime_id = "replacement"

        def __init__(self, **_kwargs):
            pass

        def hello(self, *, timeout):
            assert timeout == 5.0
            return {"ok": True}

        def close(self):
            pass

    dead = DeadHost()
    runtime = AdaptiveInferenceRuntime(
        model_packs=FakeModelPacks(),
        runtime_packs=FakeRuntimePacks(),
        host_path=host_path,
    )
    runtime._active = dead
    runtime._active_key = (
        plan().pack_id,
        plan().model,
        plan().backend.value,
        plan().context_window,
    )
    monkeypatch.setattr(polish_runtime, "NamedPipeLlamaRuntime", ReplacementHost)

    assert runtime.prepare(plan(), consent=True)
    assert dead.closed
    assert isinstance(runtime._active, ReplacementHost)


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


def test_adaptive_runtime_identity_changes_between_fallback_and_bundled_host():
    fallback = SimpleNamespace(runtime_id="fallback:1")
    value = AdaptiveInferenceRuntime(fallback=fallback)

    fallback_identity = value.runtime_id
    value._active = SimpleNamespace(runtime_id="named-pipe:2")
    value._active_key = (
        "gemma4-e4b-qat-v1",
        "gemma4:e4b-it-qat",
        "cuda",
        2048,
    )
    active_identity = value.runtime_id

    assert fallback_identity == "adaptive:fallback:fallback:1"
    assert active_identity != fallback_identity
    assert "named-pipe:2" in active_identity
    assert "gemma4-e4b-qat-v1" in active_identity
    assert "gemma4:e4b-it-qat" in active_identity
    assert "backend=cuda" in active_identity
    assert "context=2048" in active_identity


def test_adaptive_runtime_has_exactly_one_worker_slot():
    assert AdaptiveInferenceRuntime().parallel_slots == 1


def test_named_pipe_runtime_id_binds_complete_runtime_identity():
    runtime = pipe_runtime()
    runtime._host_version = "2.1.0"

    identity = runtime.runtime_id

    assert "host=2.1.0" in identity
    assert f"host_sha256={'b' * 64}" in identity
    assert "runtime=llama-cuda-test@1" in identity
    assert f"components={'c' * 64}" in identity
    assert f"pack={plan().pack_id}" in identity
    assert f"model={plan().model}" in identity
    assert "backend=cuda" in identity
    assert "context=2048" in identity


def test_named_pipe_runtime_id_changes_when_host_bytes_change(tmp_path):
    host = tmp_path / "Manuscript.PolishHost.exe"
    host.write_bytes(b"host-a")
    first_digest = polish_runtime._file_sha256(host)
    host.write_bytes(b"host-b")
    second_digest = polish_runtime._file_sha256(host)

    first = pipe_runtime(host_digest=first_digest)
    second = pipe_runtime(host_digest=second_digest)

    assert first_digest != second_digest
    assert first.runtime_id != second.runtime_id
    assert first_digest in first.runtime_id
    assert second_digest in second.runtime_id


def test_named_pipe_runtime_id_changes_when_signed_component_digest_changes(
    tmp_path,
):
    server = tmp_path / "llama-server.exe"
    first_fingerprint = polish_runtime._runtime_component_fingerprint(
        fake_runtime_pack(server, component_digest="1" * 64)
    )
    second_fingerprint = polish_runtime._runtime_component_fingerprint(
        fake_runtime_pack(server, component_digest="2" * 64)
    )

    first = pipe_runtime(component_fingerprint=first_fingerprint)
    second = pipe_runtime(component_fingerprint=second_fingerprint)

    assert first_fingerprint != second_fingerprint
    assert first.runtime_id != second.runtime_id
    assert first_fingerprint in first.runtime_id
    assert second_fingerprint in second.runtime_id


def test_adaptive_status_quarantines_failed_host_before_fallback():
    observed_runtime_ids = []

    class BrokenHost:
        runtime_id = "broken-host:1"
        is_alive = True

        def __init__(self):
            self.closed = False

        def status(self, _model, *, timeout):
            assert timeout == 0.25
            raise RuntimeError("bundled host stopped")

        def close(self):
            self.closed = True

    class Fallback:
        runtime_id = "fallback:2"

        def status(self, model, *, timeout):
            assert model == plan().model
            assert timeout == 0.25
            observed_runtime_ids.append(runtime.runtime_id)
            return SimpleNamespace(model=model, loaded=True)

    broken = BrokenHost()
    runtime = AdaptiveInferenceRuntime(fallback=Fallback())
    key = (
        plan().pack_id,
        plan().model,
        plan().backend.value,
        plan().context_window,
    )
    runtime._active = broken
    runtime._active_key = key

    assert "broken-host:1" in runtime.runtime_id
    result = runtime.status(plan().model, timeout=0.25)

    assert result.loaded
    assert broken.closed
    assert runtime._active is None
    assert runtime._active_key is None
    assert key in runtime._prepare_failures
    assert runtime._prepare_failure_reasons[key] == "bundled host stopped"
    assert runtime.last_downgrade_reason == "bundled host stopped"
    assert observed_runtime_ids == ["adaptive:fallback:fallback:2"]
    assert runtime.runtime_id == "adaptive:fallback:fallback:2"


def test_adaptive_runtime_rejects_backend_probe_that_failed_correctness():
    class FalsePositiveRuntime:
        def acceptance_probe(self, _plan):
            return AcceptanceProbeResult(
                accepted=True,
                elapsed_seconds=0.5,
                correctness_passed=False,
                protected_passed=True,
                backend=RuntimeBackend.CUDA,
            )

    runtime = AdaptiveInferenceRuntime(fallback=FalsePositiveRuntime())

    result = runtime.acceptance_probe(plan())

    assert not result.accepted
    assert "correctness" in result.reason.casefold()


def test_adaptive_runtime_rejects_backend_probe_over_warm_latency_limit():
    class FalsePositiveRuntime:
        def acceptance_probe(self, _plan):
            return AcceptanceProbeResult(
                accepted=True,
                elapsed_seconds=5.01,
                correctness_passed=True,
                protected_passed=True,
                backend=RuntimeBackend.CUDA,
            )

    runtime = AdaptiveInferenceRuntime(fallback=FalsePositiveRuntime())

    result = runtime.acceptance_probe(plan())

    assert not result.accepted
    assert "warm" in result.reason.casefold()
    assert "5.01" in result.reason


def test_adaptive_runtime_keeps_probe_model_identity_for_diagnostics():
    class DiagnosticRuntime:
        def acceptance_probe(self, execution_plan):
            return AcceptanceProbeResult(
                accepted=True,
                elapsed_seconds=0.5,
                correctness_passed=True,
                protected_passed=True,
                backend=RuntimeBackend.CUDA,
                model_identity=execution_plan.model,
            )

    runtime = AdaptiveInferenceRuntime(fallback=DiagnosticRuntime())

    result = runtime.acceptance_probe(plan())

    assert result.model_identity == plan().model


@pytest.mark.parametrize("reported_model", ["", "gemma4:e2b-it-qat"])
def test_adaptive_runtime_rejects_missing_or_wrong_probe_model_identity(
    reported_model,
):
    class FalsePositiveRuntime:
        def acceptance_probe(self, _execution_plan):
            return AcceptanceProbeResult(
                accepted=True,
                elapsed_seconds=0.5,
                correctness_passed=True,
                protected_passed=True,
                backend=RuntimeBackend.CUDA,
                model_identity=reported_model,
            )

    runtime = AdaptiveInferenceRuntime(fallback=FalsePositiveRuntime())

    result = runtime.acceptance_probe(plan())

    assert not result.accepted
    assert "model identity" in result.reason.casefold()


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
    assert calls[0] == ("load", "gemma4:e4b-it-qat", 90.0)
    assert [call[0] for call in calls[1:]] == ["generate", "generate", "generate"]
    assert all(call[2] == 30.0 for call in calls[1:])
    assert all(
        call[1].startswith("The report are not ready, and it fail.")
        for call in calls[1:]
    )


def test_ollama_acceptance_probe_fails_when_generation_identity_is_absent():
    class MissingIdentityAssistant:
        def require_gpu(self, _model, **_kwargs):
            pass

        def run(self, _text, **_kwargs):
            return SimpleNamespace(
                text=(
                    "The report is not ready, and it failed. Keep build 47 at "
                    "http://127.0.0.1:7777/protected until July 23, 2026."
                )
            )

        def runtime_status(self, model, *, timeout):
            return SimpleNamespace(
                model=model,
                loaded=True,
                backend="GPU",
                model_bytes=9,
                gpu_bytes=9,
            )

    result = OllamaInferenceRuntime(
        assistant=MissingIdentityAssistant()
    ).acceptance_probe(plan())

    assert not result.accepted
    assert result.model_identity == ""
    assert "model identity" in result.reason.casefold()


def test_ollama_acceptance_probe_fails_when_status_identity_is_absent():
    class MissingStatusIdentityAssistant:
        def require_gpu(self, _model, **_kwargs):
            pass

        def run(self, _text, **kwargs):
            return SimpleNamespace(
                text=(
                    "The report is not ready, and it failed. Keep build 47 at "
                    "http://127.0.0.1:7777/protected until July 23, 2026."
                ),
                model=kwargs["model"],
            )

        def runtime_status(self, _model, *, timeout):
            return SimpleNamespace(
                loaded=True,
                backend="GPU",
                model_bytes=9,
                gpu_bytes=9,
            )

    result = OllamaInferenceRuntime(
        assistant=MissingStatusIdentityAssistant()
    ).acceptance_probe(plan())

    assert not result.accepted
    assert result.model_identity == ""
    assert "model identity" in result.reason.casefold()


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


@pytest.mark.parametrize("reported_model", ["", "gemma4:e2b-it-qat"])
def test_ollama_generation_rejects_missing_or_wrong_primary_model_identity(
    reported_model,
):
    class WrongModelAssistant:
        def run(self, _text, **_kwargs):
            return SimpleNamespace(text="Dogs are good.", model=reported_model)

        def runtime_status(self, model, *, timeout):
            return SimpleNamespace(
                model=model,
                loaded=True,
                backend="GPU",
                model_bytes=9,
                gpu_bytes=9,
            )

    runtime = OllamaInferenceRuntime(assistant=WrongModelAssistant())

    with pytest.raises(RuntimeError, match="model identity"):
        runtime.generate(_generation_request(plan()))


@pytest.mark.parametrize("reported_model", ["", "gemma4:e2b-it-qat"])
def test_adaptive_fallback_generation_rejects_model_identity_mismatch(
    reported_model,
):
    class WrongFallback:
        def generate(self, _request):
            return SimpleNamespace(
                text="Dogs are good.",
                model=reported_model,
            )

    runtime = AdaptiveInferenceRuntime(fallback=WrongFallback())

    with pytest.raises(RuntimeError, match="model identity"):
        runtime.generate(_generation_request(plan()))


def test_adaptive_runtime_does_not_close_host_during_parallel_generation():
    entered = threading.Barrier(3)
    release = threading.Event()

    class BlockingRuntime:
        runtime_id = "blocking:1"

        def __init__(self):
            self.closed = False
            self.closed_while_running = False
            self.running = 0
            self.lock = threading.Lock()

        def generate(self, request):
            with self.lock:
                self.running += 1
            entered.wait(timeout=2.0)
            release.wait(timeout=2.0)
            with self.lock:
                self.running -= 1
            return SimpleNamespace(text="Dogs are good.", model=request.plan.model)

        def close(self):
            with self.lock:
                self.closed_while_running = self.running > 0
                self.closed = True

    host = BlockingRuntime()
    runtime = AdaptiveInferenceRuntime(fallback=SimpleNamespace())
    runtime._active = host
    runtime._active_key = (
        plan().pack_id,
        plan().model,
        plan().backend.value,
        plan().context_window,
    )
    retire_entered = threading.Event()
    retire = runtime._retire

    def observed_retire(value):
        retire_entered.set()
        retire(value)

    runtime._retire = observed_retire
    results = []

    workers = [
        threading.Thread(
            target=lambda: results.append(
                runtime.generate(_generation_request(plan()))
            )
        )
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    entered.wait(timeout=2.0)

    closer = threading.Thread(target=runtime.close)
    closer.start()

    assert retire_entered.wait(timeout=2.0)
    assert not host.closed
    release.set()
    for worker in workers:
        worker.join(timeout=2.0)
    closer.join(timeout=2.0)

    assert len(results) == 2
    assert host.closed
    assert not host.closed_while_running


def test_adaptive_runtime_retirement_is_bounded_when_lease_never_returns():
    class StuckHost:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    host = StuckHost()
    runtime = AdaptiveInferenceRuntime(fallback=SimpleNamespace())
    runtime._retire_timeout_seconds = 0.05
    runtime._active_leases[id(host)] = 1
    started = polish_runtime.time.monotonic()

    runtime._retire(host)

    elapsed = polish_runtime.time.monotonic() - started
    assert elapsed < 0.5
    assert host.closed


def _generation_request(execution_plan: ExecutionPlan) -> GenerationRequest:
    return GenerationRequest(
        text="Dogs is good.",
        command="Proofread",
        profile="General",
        instruction="",
        language="",
        protected_terms=(),
        plan=execution_plan,
    )


class _StandbyAssistant:
    def __init__(self, *, cpu_models=()):
        self.cpu_models = set(cpu_models)
        self.calls = []

    def require_gpu(self, model, **kwargs):
        self.calls.append(("load", model, kwargs))
        if model in self.cpu_models:
            raise RuntimeError("GPU-only proofreading stopped")

    def runtime_status(self, model, *, timeout):
        backend = "CPU" if model in self.cpu_models else "GPU"
        return SimpleNamespace(
            model=model,
            loaded=True,
            backend=backend,
            model_bytes=9,
            gpu_bytes=0 if backend == "CPU" else 9,
        )

    def run(self, text, **kwargs):
        model = kwargs["model"]
        self.calls.append(("run", model, text, kwargs))
        if text == "Dogs is good." and model == plan().model:
            raise RuntimeError("AI generation timed out")
        if text == "Dogs is good.":
            return SimpleNamespace(text="Dogs are good.", model=model)
        return SimpleNamespace(
            text=(
                "The report is not ready, and it failed. Keep build 47 at "
                "http://127.0.0.1:7777/protected until July 23, 2026."
            ),
            model=model,
        )


def test_ollama_never_uses_standby_without_its_own_accepted_probe():
    assistant = _StandbyAssistant()
    runtime = OllamaInferenceRuntime(assistant=assistant)
    execution_plan = replace(plan(), standby_model="gemma4:e2b-it-qat")

    with pytest.raises(RuntimeError, match="timed out"):
        runtime.generate(_generation_request(execution_plan))

    correction_models = [
        call[1]
        for call in assistant.calls
        if call[0] == "run" and call[2] == "Dogs is good."
    ]
    assert correction_models == [plan().model, plan().model]


def test_ollama_probes_standby_independently_before_using_it():
    assistant = _StandbyAssistant()
    runtime = OllamaInferenceRuntime(assistant=assistant)
    execution_plan = replace(plan(), standby_model="gemma4:e2b-it-qat")

    assert runtime.acceptance_probe(execution_plan).accepted
    standby_probe_calls = [
        call
        for call in assistant.calls
        if call[0] == "run"
        and call[1] == execution_plan.standby_model
        and call[2].startswith("The report are not ready")
    ]
    assert len(standby_probe_calls) == 3

    result = runtime.generate(_generation_request(execution_plan))

    assert result.text == "Dogs are good."
    assert result.model == execution_plan.standby_model


def test_ollama_does_not_reuse_probe_for_different_standby_identity():
    assistant = _StandbyAssistant()
    runtime = OllamaInferenceRuntime(assistant=assistant)
    accepted_plan = replace(plan(), standby_model="gemma4:e2b-it-qat")
    assert runtime.acceptance_probe(accepted_plan).accepted
    changed_plan = replace(accepted_plan, standby_model="gemma4:12b-it-q4_K_M")

    with pytest.raises(RuntimeError, match="timed out"):
        runtime.generate(_generation_request(changed_plan))

    changed_corrections = [
        call
        for call in assistant.calls
        if call[0] == "run"
        and call[1] == changed_plan.standby_model
        and call[2] == "Dogs is good."
    ]
    assert changed_corrections == []


def test_ollama_rejects_cpu_standby_for_gpu_required_plan():
    standby = "gemma4:e2b-it-qat"
    assistant = _StandbyAssistant(cpu_models=(standby,))
    runtime = OllamaInferenceRuntime(assistant=assistant)
    execution_plan = replace(plan(), standby_model=standby)

    assert runtime.acceptance_probe(execution_plan).accepted
    with pytest.raises(RuntimeError, match="timed out"):
        runtime.generate(_generation_request(execution_plan))

    standby_corrections = [
        call
        for call in assistant.calls
        if call[0] == "run" and call[1] == standby and call[2] == "Dogs is good."
    ]
    assert standby_corrections == []


def test_ollama_keeps_gpu_requirement_on_accepted_standby_generation():
    assistant = _StandbyAssistant()
    runtime = OllamaInferenceRuntime(assistant=assistant)
    execution_plan = replace(plan(), standby_model="gemma4:e2b-it-qat")
    assert runtime.acceptance_probe(execution_plan).accepted

    runtime.generate(_generation_request(execution_plan))

    standby_correction = next(
        call
        for call in assistant.calls
        if call[0] == "run"
        and call[1] == execution_plan.standby_model
        and call[2] == "Dogs is good."
    )
    assert standby_correction[3]["require_gpu"] is True
