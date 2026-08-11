from __future__ import annotations

import atexit
import ctypes
import getpass
import hashlib
import json
import os
import secrets
import shutil
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

from .models import OLLAMA_KEEP_ALIVE
from .paths import resource_path
from .polish_model_packs import (
    ModelPackManager,
    ModelPackNotFoundError,
    ModelPackSecurityError,
)
from .polish_runtime_packs import (
    RuntimePackConsentRequired,
    RuntimePackError,
    RuntimePackManager,
)
from .polish_types import (
    GPU_BACKENDS,
    AcceptanceProbeResult,
    GenerationRequest,
    GenerationResult,
    RuntimeBackend,
    RuntimeStatus,
)
from .selected_text import LocalWritingAssistant
from .writing_models import LocalWritingModelManager




def _same_model_identity(left: object, right: object) -> bool:
    left_name = str(left or "").strip()
    right_name = str(right or "").strip()
    return bool(left_name) and bool(right_name) and left_name == right_name


def _plan_runtime_key(plan) -> tuple[str, str, str, int]:
    return (
        str(getattr(plan, "pack_id", "") or "").strip(),
        str(getattr(plan, "model", "") or "").strip(),
        RuntimeBackend(getattr(plan, "backend", RuntimeBackend.RULES)).value,
        int(getattr(plan, "context_window", 0) or 0),
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_component_fingerprint(pack) -> str:
    records: list[tuple[str, int, str]] = []
    for component in tuple(getattr(pack, "components", ()) or ()):
        filename = str(getattr(component, "filename", "") or "").strip()
        digest = str(getattr(component, "sha256", "") or "").strip().casefold()
        size = int(getattr(component, "size_bytes", 0) or 0)
        if (
            not filename
            or len(digest) != 64
            or any(value not in "0123456789abcdef" for value in digest)
            or size <= 0
        ):
            raise RuntimeError("Signed runtime component receipt is invalid.")
        records.append((filename, size, digest))
    if not records:
        raise RuntimeError("Signed runtime pack has no component receipts.")
    encoded = json.dumps(
        sorted(records),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class OllamaInferenceRuntime:
    """Phase 1 local runtime. The Polish engine does not depend on Ollama."""

    def __init__(
        self,
        *,
        assistant: LocalWritingAssistant | None = None,
        manager: LocalWritingModelManager | None = None,
    ) -> None:
        self.assistant = assistant or LocalWritingAssistant()
        self.manager = manager or LocalWritingModelManager()
        self._runtime_id = ""
        self._accepted_standby_probes: set[tuple[object, ...]] = set()
        self._standby_probe_failures: dict[tuple[object, ...], str] = {}

    @property
    def runtime_id(self) -> str:
        if not self._runtime_id:
            try:
                version = self.manager.server_version(timeout=1.0)
            except (OSError, RuntimeError, ValueError):
                version = "unavailable"
            self._runtime_id = f"ollama:{version}"
        return self._runtime_id

    def generate(self, request: GenerationRequest) -> GenerationResult:
        cold_started = time.perf_counter()
        cold_load_seconds = 0.0
        if request.plan.require_full_gpu:
            status = self.status(request.plan.model, timeout=1.0)
            if not status.loaded:
                self.assistant.require_gpu(
                    request.plan.model,
                    timeout=request.plan.cold_load_timeout_seconds,
                    keep_alive=OLLAMA_KEEP_ALIVE,
                    context_window=request.plan.context_window,
                )
                cold_load_seconds = max(
                    0.0,
                    time.perf_counter() - cold_started,
                )
        started = time.perf_counter()
        used_standby = False
        try:
            result = self.assistant.run(
                request.text,
                command=request.command,
                model=request.plan.model,
                instruction=request.instruction,
                language=request.language,
                profile=request.profile,
                protected_terms=request.protected_terms,
                timeout=request.plan.absolute_timeout_seconds,
                keep_alive=OLLAMA_KEEP_ALIVE,
                context_window=request.plan.context_window,
                max_tokens=request.plan.max_output_tokens,
                require_gpu=request.plan.require_full_gpu,
                defer_meaning_validation=True,
            )
        except RuntimeError as exc:
            if "timed out" not in str(exc).casefold():
                raise
            # Other GPU applications can evict the model between requests;
            # the request then dies at its normal ceiling while the server
            # reloads. Reload explicitly once and retry with an extended
            # ceiling instead of failing a whole review section.
            self.assistant.require_gpu(
                request.plan.model,
                timeout=request.plan.cold_load_timeout_seconds,
                keep_alive=OLLAMA_KEEP_ALIVE,
                context_window=request.plan.context_window,
            )
            try:
                result = self.assistant.run(
                    request.text,
                    command=request.command,
                    model=request.plan.model,
                    instruction=request.instruction,
                    language=request.language,
                    profile=request.profile,
                    protected_terms=request.protected_terms,
                    timeout=max(
                        request.plan.absolute_timeout_seconds * 1.5,
                        request.plan.absolute_timeout_seconds + 15.0,
                    ),
                    keep_alive=OLLAMA_KEEP_ALIVE,
                    context_window=request.plan.context_window,
                    max_tokens=request.plan.max_output_tokens,
                    require_gpu=request.plan.require_full_gpu,
                    defer_meaning_validation=True,
                )
            except RuntimeError as retry_exc:
                if (
                    "timed out" not in str(retry_exc).casefold()
                    or not request.plan.standby_model
                    or self._standby_probe_identity(request.plan)
                    not in self._accepted_standby_probes
                ):
                    raise
                used_standby = True
                result = self.assistant.run(
                    request.text,
                    command=request.command,
                    model=request.plan.standby_model,
                    instruction=request.instruction,
                    language=request.language,
                    profile=request.profile,
                    protected_terms=request.protected_terms,
                    timeout=request.plan.standby_timeout_seconds,
                    keep_alive=OLLAMA_KEEP_ALIVE,
                    context_window=request.plan.context_window,
                    max_tokens=request.plan.max_output_tokens,
                    require_gpu=request.plan.require_full_gpu,
                    defer_meaning_validation=True,
                )
        elapsed = max(0.0, time.perf_counter() - started)
        expected_model = (
            request.plan.standby_model if used_standby else request.plan.model
        )
        reported_model = str(getattr(result, "model", "") or "").strip()
        if not _same_model_identity(reported_model, expected_model):
            raise RuntimeError("Local AI returned the wrong model identity.")
        status = self.status(reported_model, timeout=1.0)
        if status.backend is not request.plan.backend:
            raise RuntimeError("Local AI returned the wrong backend identity.")
        return GenerationResult(
            text=result.text,
            model=reported_model,
            backend=status.backend,
            elapsed_seconds=elapsed,
            cold_load_seconds=cold_load_seconds,
        )

    def status(self, model: str, *, timeout: float = 1.0) -> RuntimeStatus:
        value = self.assistant.runtime_status(model, timeout=timeout)
        backend = {
            "GPU": RuntimeBackend.CUDA,
            "CPU": RuntimeBackend.CPU,
            "Hybrid": RuntimeBackend.RULES,
            "Loading": RuntimeBackend.RULES,
        }.get(value.backend, RuntimeBackend.RULES)
        return RuntimeStatus(
            model=str(getattr(value, "model", "") or "").strip(),
            loaded=value.loaded,
            backend=backend,
            model_bytes=value.model_bytes,
            gpu_bytes=value.gpu_bytes,
            detail=value.backend,
        )

    def acceptance_probe(self, plan) -> AcceptanceProbeResult:
        primary = self._acceptance_probe_model(plan, plan.model)
        identity = self._standby_probe_identity(plan)
        if identity is not None:
            self._accepted_standby_probes.discard(identity)
            standby = self._acceptance_probe_model(plan, plan.standby_model)
            if self._probe_is_fully_accepted(plan, standby):
                self._accepted_standby_probes.add(identity)
                self._standby_probe_failures.pop(identity, None)
            else:
                self._standby_probe_failures[identity] = (
                    standby.reason or "Standby acceptance probe failed."
                )
        return primary

    def _acceptance_probe_model(self, plan, model: str) -> AcceptanceProbeResult:
        if not plan.use_llm:
            return AcceptanceProbeResult(
                accepted=True,
                elapsed_seconds=0.0,
                correctness_passed=True,
                protected_passed=True,
                backend=RuntimeBackend.RULES,
                reason="Rules-only plan.",
                model_identity="",
            )
        try:
            if plan.require_full_gpu:
                self.assistant.require_gpu(
                    model,
                    timeout=plan.cold_load_timeout_seconds,
                    keep_alive=OLLAMA_KEEP_ALIVE,
                    context_window=min(2048, plan.context_window),
                )
            source = (
                "The report are not ready, and it fail. Keep build 47 at "
                "http://127.0.0.1:7777/protected until July 23, 2026."
            )
            elapsed_samples: list[float] = []
            correctness_samples: list[bool] = []
            protected_samples: list[bool] = []
            observed_models: list[str] = []
            for _sample in range(3):
                started = time.perf_counter()
                result = self.assistant.run(
                    source,
                    command="Proofread",
                    model=model,
                    instruction=(
                        "Correct every grammar error. Keep the date, build number, URL, "
                        "and negative statement exact."
                    ),
                    profile="General",
                    timeout=plan.absolute_timeout_seconds,
                    keep_alive=OLLAMA_KEEP_ALIVE,
                    context_window=min(2048, plan.context_window),
                    max_tokens=min(128, plan.max_output_tokens),
                    require_gpu=plan.require_full_gpu,
                )
                elapsed_samples.append(
                    max(0.0, time.perf_counter() - started)
                )
                observed_models.append(
                    str(getattr(result, "model", "") or "").strip()
                )
                folded = " ".join(result.text.casefold().split())
                correctness_samples.append(
                    "report is not ready" in folded
                    and (
                        "it fails" in folded or "it failed" in folded
                    )
                    and "build 47" in folded
                )
                protected_samples.append(
                    all(
                        value in result.text
                        for value in (
                            "July 23, 2026",
                            "47",
                            "http://127.0.0.1:7777/protected",
                        )
                    )
                )
            elapsed = sorted(elapsed_samples)[1]
            correctness = sum(correctness_samples) >= 2
            protected = all(protected_samples)
            status = self.status(model, timeout=1.0)
            backend_ok = status.backend is plan.backend
            identity_ok = all(
                _same_model_identity(observed, model)
                for observed in observed_models
            ) and _same_model_identity(status.model, model)
            latency_ok = elapsed <= plan.warm_latency_limit_seconds
            # Correctness and latency rank packs; they no longer decide whether
            # the local AI may run at all. The correctness probe demands three
            # literal phrases, so a model that answers "the report has not been
            # completed and it failed" - correct English, correct meaning -
            # scored as broken and turned the AI off for the whole session.
            # Protected facts and GPU residency stay decisive because those are
            # about the model damaging text or the machine, not its phrasing.
            accepted = protected and backend_ok and identity_ok
            reasons: list[str] = []
            if not correctness:
                reasons.append("correctness probe failed")
            if not protected:
                reasons.append("protected facts changed")
            if not backend_ok:
                reasons.append("backend identity did not match the plan")
            if not identity_ok:
                reasons.append("model identity changed during probe")
            if not latency_ok:
                reasons.append(
                    f"median warm probe took {elapsed:.2f}s; limit is "
                    f"{plan.warm_latency_limit_seconds:.2f}s"
                )
            return AcceptanceProbeResult(
                accepted=accepted,
                elapsed_seconds=elapsed,
                correctness_passed=correctness,
                protected_passed=protected,
                backend=status.backend,
                reason="; ".join(reasons),
                model_identity=model if identity_ok else "",
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return AcceptanceProbeResult(
                accepted=False,
                elapsed_seconds=0.0,
                correctness_passed=False,
                protected_passed=False,
                backend=RuntimeBackend.RULES,
                reason=str(exc)[:300],
                model_identity=str(model).strip(),
            )

    @staticmethod
    def _standby_probe_identity(plan) -> tuple[object, ...] | None:
        model = str(getattr(plan, "standby_model", "") or "").strip()
        if not model:
            return None
        return (
            model.casefold(),
            str(plan.backend.value),
            int(plan.context_window),
            int(plan.max_output_tokens),
            bool(plan.require_full_gpu),
            float(plan.warm_latency_limit_seconds),
            int(plan.policy_version),
        )

    @staticmethod
    def _probe_is_fully_accepted(plan, probe: AcceptanceProbeResult) -> bool:
        latency_limit = max(0.0, float(plan.warm_latency_limit_seconds))
        latency_ok = latency_limit == 0.0 or probe.elapsed_seconds <= latency_limit
        backend_ok = probe.backend is plan.backend
        identity_ok = _same_model_identity(
            probe.model_identity,
            plan.standby_model,
        )
        return (
            probe.accepted
            and probe.correctness_passed
            and probe.protected_passed
            and latency_ok
            and backend_ok
            and identity_ok
        )


class NamedPipeLlamaRuntime:
    """Client and lifecycle owner for the separately built llama.cpp host."""

    MAX_MESSAGE_BYTES = 8 * 1024 * 1024

    def __init__(
        self,
        pipe_name: str | None = None,
        *,
        host_path: str | Path | None = None,
        server_path: str | Path | None = None,
        model_path: str | Path | None = None,
        model_pack_id: str = "",
        model_name: str = "",
        backend: RuntimeBackend = RuntimeBackend.CPU,
        context_window: int = 4096,
        idle_seconds: int = 300,
        runtime_pack_id: str = "",
        runtime_pack_version: str = "",
        host_executable_sha256: str = "",
        runtime_component_fingerprint: str = "",
        secret: str | None = None,
    ) -> None:
        suffix = hashlib.sha256(
            (
                f"{getpass.getuser()}|{os.getpid()}|"
                f"{secrets.token_hex(16)}"
            ).encode("utf-8")
        ).hexdigest()[:24]
        self.pipe_name = pipe_name or rf"\\.\pipe\ManuscriptPolish-{suffix}"
        if not self.pipe_name.startswith(r"\\.\pipe\ManuscriptPolish-"):
            raise ValueError("llama.cpp pipe name is not Manuscript-owned.")
        self.host_path = Path(host_path).resolve() if host_path else None
        self.server_path = Path(server_path).resolve() if server_path else None
        self.model_path = Path(model_path).resolve() if model_path else None
        self.model_pack_id = str(model_pack_id).strip()
        self.model_name = str(model_name).strip()
        self.backend = RuntimeBackend(backend)
        self.context_window = max(512, min(32_768, int(context_window)))
        self.idle_seconds = max(60, min(3600, int(idle_seconds)))
        self.runtime_pack_id = str(runtime_pack_id).strip()
        self.runtime_pack_version = str(runtime_pack_version).strip()
        self.host_executable_sha256 = str(host_executable_sha256).strip().casefold()
        self.runtime_component_fingerprint = str(
            runtime_component_fingerprint
        ).strip().casefold()
        for label, value in (
            ("host executable", self.host_executable_sha256),
            ("runtime components", self.runtime_component_fingerprint),
        ):
            if value and (
                len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError(f"llama.cpp {label} fingerprint is invalid.")
        self._host_version = "unverified"
        self._secret = secret or secrets.token_hex(32)
        if len(self._secret) != 64 or any(
            value not in "0123456789abcdefABCDEF" for value in self._secret
        ):
            raise ValueError("llama.cpp pipe secret is invalid.")
        self._process: subprocess.Popen | None = None
        self._launch_lock = threading.Lock()
        self._closed = False
        atexit.register(self.close)

    @property
    def runtime_id(self) -> str:
        runtime_pack = self.runtime_pack_id or "unverified"
        runtime_version = self.runtime_pack_version or "unverified"
        return (
            f"llama.cpp:named-pipe:host={self._host_version}:"
            f"host_sha256={self.host_executable_sha256 or 'unverified'}:"
            f"runtime={runtime_pack}@{runtime_version}:"
            f"components={self.runtime_component_fingerprint or 'unverified'}:"
            f"pack={self.model_pack_id or 'unverified'}:"
            f"model={self.model_name or 'unverified'}:"
            f"backend={self.backend.value}:context={self.context_window}"
        )

    @property
    def is_alive(self) -> bool:
        process = self._process
        return bool(not self._closed and process is not None and process.poll() is None)

    def hello(self, *, timeout: float = 5.0) -> dict:
        payload = self._exchange({"type": "hello"}, timeout=timeout)
        self._validate_host_identity(payload)
        version = str(payload.get("host_version") or "").strip()
        if not version:
            raise RuntimeError("Bundled llama.cpp host version is missing.")
        self._host_version = version
        return payload

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if _plan_runtime_key(request.plan) != self.plan_key:
            raise RuntimeError("llama.cpp request does not match the active runtime plan.")
        started = time.perf_counter()
        payload = self._exchange(
            {
                "type": "generate",
                "model_pack_id": request.plan.pack_id,
                "model": request.plan.model,
                "backend": request.plan.backend.value,
                "context_window": request.plan.context_window,
                "max_output_tokens": request.plan.max_output_tokens,
                "timeout_seconds": request.plan.absolute_timeout_seconds,
                "text": request.text,
                "command": request.command,
                "profile": request.profile,
                "instruction": request.instruction,
                "language": request.language,
                "protected_terms": list(request.protected_terms),
            }
        )
        if payload.get("ok") is not True or not isinstance(payload.get("text"), str):
            raise RuntimeError(str(payload.get("error") or "llama.cpp host failed."))
        reported_model = str(payload.get("model") or "").strip()
        if not _same_model_identity(reported_model, request.plan.model):
            raise RuntimeError("llama.cpp host returned the wrong model identity.")
        try:
            backend = RuntimeBackend(str(payload.get("backend") or ""))
        except ValueError as exc:
            raise RuntimeError("llama.cpp host backend identity is invalid.") from exc
        if backend is not self.backend or backend is not request.plan.backend:
            raise RuntimeError("llama.cpp host returned the wrong backend identity.")
        if (
            backend in GPU_BACKENDS
            and payload.get("full_gpu_resident") is not True
        ):
            raise RuntimeError("AI left the GPU. GPU-only correction stopped.")
        return GenerationResult(
            text=payload["text"],
            model=reported_model,
            backend=backend,
            elapsed_seconds=max(0.0, time.perf_counter() - started),
            input_tokens=max(0, int(payload.get("input_tokens") or 0)),
            output_tokens=max(0, int(payload.get("output_tokens") or 0)),
        )

    def status(self, model: str, *, timeout: float = 1.0) -> RuntimeStatus:
        payload = self._exchange(
            {
                "type": "status",
                "model": model,
                "backend": self.backend.value,
                "context_window": self.context_window,
                "timeout_seconds": max(0.2, min(5.0, float(timeout))),
            },
            timeout=timeout,
        )
        reported_model = str(payload.get("model") or "").strip()
        if not _same_model_identity(reported_model, self.model_name):
            raise RuntimeError("llama.cpp host returned the wrong model identity.")
        try:
            backend = RuntimeBackend(str(payload.get("backend") or ""))
        except ValueError as exc:
            raise RuntimeError("llama.cpp host backend identity is invalid.") from exc
        if backend is not self.backend:
            raise RuntimeError("llama.cpp host returned the wrong backend identity.")
        loaded = bool(payload.get("loaded"))
        if (
            loaded
            and self.backend in GPU_BACKENDS
            and payload.get("full_gpu_resident") is not True
        ):
            raise RuntimeError("llama.cpp host did not prove full GPU residency.")
        return RuntimeStatus(
            model=reported_model,
            loaded=loaded,
            backend=backend,
            model_bytes=max(0, int(payload.get("model_bytes") or 0)),
            gpu_bytes=max(0, int(payload.get("gpu_bytes") or 0)),
            detail=str(payload.get("detail") or ""),
        )

    def acceptance_probe(self, plan) -> AcceptanceProbeResult:
        if _plan_runtime_key(plan) != self.plan_key:
            raise RuntimeError("llama.cpp probe does not match the active runtime plan.")
        payload = self._exchange(
            {
                "type": "acceptance_probe",
                "model_pack_id": plan.pack_id,
                "model": plan.model,
                "backend": plan.backend.value,
                "context_window": plan.context_window,
                "latency_limit_seconds": plan.warm_latency_limit_seconds,
            },
            timeout=plan.absolute_timeout_seconds,
        )
        reported_model = str(payload.get("model") or "").strip()
        identity_ok = _same_model_identity(reported_model, plan.model)
        try:
            reported_backend = RuntimeBackend(str(payload.get("backend") or ""))
        except ValueError:
            reported_backend = RuntimeBackend.RULES
        backend_ok = reported_backend is self.backend and reported_backend is plan.backend
        residency_ok = (
            reported_backend not in GPU_BACKENDS
            or payload.get("full_gpu_resident") is True
        )
        reasons = [str(payload.get("reason") or "").strip()]
        if not identity_ok:
            reasons.append("Model identity was missing or did not match the plan.")
        if not backend_ok:
            reasons.append("Backend identity was missing or did not match the plan.")
        if not residency_ok:
            reasons.append("Full GPU residency was not proved.")
        return AcceptanceProbeResult(
            accepted=(
                bool(payload.get("accepted"))
                and identity_ok
                and backend_ok
                and residency_ok
            ),
            elapsed_seconds=max(0.0, float(payload.get("elapsed_seconds") or 0.0)),
            correctness_passed=bool(payload.get("correctness_passed")),
            protected_passed=bool(payload.get("protected_passed")),
            backend=reported_backend,
            reason="; ".join(value for value in reasons if value),
            model_identity=reported_model,
        )

    @property
    def plan_key(self) -> tuple[str, str, str, int]:
        return (
            self.model_pack_id,
            self.model_name,
            self.backend.value,
            self.context_window,
        )

    def _validate_host_identity(self, payload: dict) -> None:
        if payload.get("ok") is not True:
            raise RuntimeError("Bundled Polish host handshake failed.")
        if str(payload.get("runtime") or "").strip() != "llama.cpp":
            raise RuntimeError("Bundled Polish host runtime identity is invalid.")
        if not _same_model_identity(payload.get("model"), self.model_name):
            raise RuntimeError("Bundled Polish host model identity is invalid.")
        if str(payload.get("model_pack_id") or "").strip() != self.model_pack_id:
            raise RuntimeError("Bundled Polish host pack identity is invalid.")
        if str(payload.get("backend") or "").strip() != self.backend.value:
            raise RuntimeError("Bundled Polish host backend identity is invalid.")
        try:
            context_window = int(payload.get("context_window"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Bundled Polish host context identity is invalid.") from exc
        if context_window != self.context_window:
            raise RuntimeError("Bundled Polish host context identity is invalid.")

    def _exchange(self, payload: dict, *, timeout: float = 30.0) -> dict:
        if self._closed:
            raise RuntimeError("Bundled llama.cpp host is closed.")
        self._ensure_host(timeout=min(5.0, max(0.2, float(timeout))))
        payload = dict(payload)
        payload["protocol_version"] = 1
        payload["auth"] = self._secret
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if len(raw) > self.MAX_MESSAGE_BYTES:
            raise ValueError("llama.cpp request is too large.")
        _wait_named_pipe(self.pipe_name, timeout)
        timer: threading.Timer | None = None
        try:
            with Path(self.pipe_name).open("r+b", buffering=0) as pipe:
                try:
                    handle = _os_file_handle(pipe)
                    timer = threading.Timer(
                        max(0.2, float(timeout)),
                        _cancel_io,
                        args=(handle,),
                    )
                    timer.daemon = True
                    timer.start()
                except (AttributeError, OSError, RuntimeError, ValueError):
                    timer = None
                pipe.write(struct.pack("<I", len(raw)))
                pipe.write(raw)
                size_raw = _read_exact(pipe, 4)
                size = struct.unpack("<I", size_raw)[0]
                if size > self.MAX_MESSAGE_BYTES:
                    raise RuntimeError("llama.cpp response is too large.")
                response = _read_exact(pipe, size)
        except OSError as exc:
            raise RuntimeError(f"Bundled llama.cpp host unavailable: {exc}") from exc
        finally:
            if timer is not None:
                timer.cancel()
        try:
            value = json.loads(response.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("llama.cpp host returned invalid JSON.") from exc
        if not isinstance(value, dict):
            raise RuntimeError("llama.cpp host returned an invalid response.")
        return value

    def _ensure_host(self, *, timeout: float) -> None:
        if self._process is not None:
            if self._process.poll() is None:
                return
            raise RuntimeError(
                f"Bundled llama.cpp host stopped with code {self._process.returncode}."
            )
        if self.host_path is None:
            return
        with self._launch_lock:
            if self._process is not None:
                if self._process.poll() is None:
                    return
                raise RuntimeError("Bundled llama.cpp host stopped during startup.")
            if (
                not self.host_path.is_file()
                or self.server_path is None
                or not self.server_path.is_file()
                or self.model_path is None
                or not self.model_path.is_file()
            ):
                raise RuntimeError("Bundled llama.cpp files are incomplete.")
            pipe_short = self.pipe_name[len("\\\\.\\pipe\\") :]
            environment = os.environ.copy()
            environment["MANUSCRIPT_POLISH_PIPE_SECRET"] = self._secret
            launcher = [str(self.host_path)]
            if self.host_path.suffix.casefold() == ".dll":
                dotnet = shutil.which("dotnet") or shutil.which("dotnet.exe")
                if not dotnet:
                    raise RuntimeError("The development Polish host needs .NET.")
                launcher = [str(dotnet), str(self.host_path)]
            command = [
                *launcher,
                "--pipe-name",
                pipe_short,
                "--server",
                str(self.server_path),
                "--runtime-root",
                str(self.server_path.parent),
                "--model",
                str(self.model_path),
                "--model-root",
                str(self.model_path.parent),
                "--model-name",
                self.model_name or self.model_path.stem,
                "--model-pack-id",
                self.model_pack_id,
                "--backend",
                self.backend.value,
                "--parent-pid",
                str(os.getpid()),
                "--idle-seconds",
                str(self.idle_seconds),
                "--context-window",
                str(self.context_window),
            ]
            try:
                self._process = subprocess.Popen(
                    command,
                    cwd=str(self.host_path.parent),
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    close_fds=True,
                    creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
                )
            except OSError as exc:
                raise RuntimeError(
                    f"Bundled llama.cpp host could not start: {exc}"
                ) from exc
        try:
            _wait_named_pipe(self.pipe_name, timeout)
        except RuntimeError:
            if self._process is not None and self._process.poll() is not None:
                detail = _host_failure_detail(self._process)
                raise RuntimeError(
                    "Bundled llama.cpp host rejected its secure configuration"
                    + (f": {detail}" if detail else ".")
                )
            raise

    def close(self) -> None:
        if self._closed:
            return
        process = self._process
        if process is not None and process.poll() is None:
            try:
                self._exchange({"type": "shutdown"}, timeout=1.0)
            except (OSError, RuntimeError, ValueError):
                pass
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
        self._closed = True


class AdaptiveInferenceRuntime:
    """Prefer bundled llama.cpp, while keeping Ollama as the rollback path."""

    def __init__(
        self,
        *,
        model_packs: ModelPackManager | None = None,
        runtime_packs: RuntimePackManager | None = None,
        fallback: OllamaInferenceRuntime | None = None,
        host_path: str | Path | None = None,
    ) -> None:
        self.model_packs = model_packs or ModelPackManager()
        self.runtime_packs = runtime_packs or RuntimePackManager()
        self.fallback = fallback or OllamaInferenceRuntime()
        self.host_path = Path(
            host_path
            or resource_path(
                "resources",
                "polish-host",
                "Manuscript.PolishHost.exe",
            )
        )
        self._active: NamedPipeLlamaRuntime | None = None
        self._active_key: tuple[str, str, str, int] | None = None
        self._prepare_failures: dict[tuple[str, str, str, int], float] = {}
        self._prepare_failure_reasons: dict[tuple[str, str, str, int], str] = {}
        self.last_downgrade_reason = ""
        self._state = threading.Condition(threading.RLock())
        self._prepare_lock = threading.Lock()
        self._active_leases: dict[int, int] = {}
        self._retire_timeout_seconds = 5.0

    @property
    def runtime_id(self) -> str:
        # Acceptance results belong to the runtime that actually executed the
        # probe. A fallback probe must never authorize the bundled host later,
        # and a bundled-host probe must never authorize a fallback process.
        with self._state:
            active = self._active
            active_key = self._active_key
        if active is not None and active_key is not None:
            active_id = str(
                getattr(active, "runtime_id", "named-pipe:v2")
            ).strip()
            pack_id, model, backend, context_window = active_key
            return (
                f"adaptive:active:{active_id}:pack={pack_id}:model={model}:"
                f"backend={backend}:context={context_window}"
            )
        fallback_id = str(
            getattr(
                self.fallback,
                "runtime_id",
                type(self.fallback).__name__,
            )
        ).strip()
        return f"adaptive:fallback:{fallback_id}"

    @property
    def parallel_slots(self) -> int:
        """One model worker preserves FIFO order and predictable latency."""
        return 1

    def prepare(
        self,
        plan,
        *,
        consent: bool,
    ) -> bool:
        key = _plan_runtime_key(plan)
        with self._prepare_lock:
            stale = None
            with self._state:
                if self._active is not None and self._active_key == key:
                    if bool(getattr(self._active, "is_alive", True)):
                        return True
                    stale = self._active
                    self._active = None
                    self._active_key = None
            self._retire(stale)
            if sys.platform != "win32":
                self.last_downgrade_reason = "Bundled llama.cpp host requires Windows."
                return False
            # A failed host probe (for example Application Control blocking the
            # executable) does not heal between requests. Re-probing on every
            # section added more than a second of dead time per correction, so a
            # failure is remembered and retried at most every five minutes.
            failed_at = self._prepare_failures.get(key, 0.0)
            if failed_at and time.monotonic() - failed_at < 300.0:
                self.last_downgrade_reason = self._prepare_failure_reasons.get(
                    key, "Bundled llama.cpp host recently failed."
                )
                return False
            runtime = None
            try:
                model_path = self.model_packs.model_file(plan.pack_id)
                runtime_pack = self.runtime_packs.ensure(
                    plan.backend,
                    consent=consent,
                )
                runtime_pack = self.runtime_packs.probe(runtime_pack)
                if not self.host_path.is_file():
                    raise RuntimeError("Bundled Polish host is not installed.")
                host_executable_sha256 = _file_sha256(self.host_path)
                runtime_component_fingerprint = _runtime_component_fingerprint(
                    runtime_pack
                )
                runtime = NamedPipeLlamaRuntime(
                    host_path=self.host_path,
                    server_path=runtime_pack.executable_path,
                    model_path=model_path,
                    model_pack_id=plan.pack_id,
                    model_name=plan.model,
                    backend=plan.backend,
                    context_window=plan.context_window,
                    idle_seconds=300,
                    runtime_pack_id=runtime_pack.pack_id,
                    runtime_pack_version=runtime_pack.version,
                    host_executable_sha256=host_executable_sha256,
                    runtime_component_fingerprint=runtime_component_fingerprint,
                )
                runtime.hello(timeout=5.0)
            except (
                ModelPackNotFoundError,
                ModelPackSecurityError,
                RuntimePackConsentRequired,
                RuntimePackError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ) as exc:
                if runtime is not None:
                    runtime.close()
                self.last_downgrade_reason = str(exc)[:300]
                self._prepare_failures[key] = time.monotonic()
                self._prepare_failure_reasons[key] = self.last_downgrade_reason
                return False
            with self._state:
                previous = self._active
                self._active = runtime
                self._active_key = key
            self.last_downgrade_reason = ""
            self._prepare_failures.pop(key, None)
            self._prepare_failure_reasons.pop(key, None)
            self._retire(previous)
            return True

    def generate(self, request: GenerationRequest) -> GenerationResult:
        runtime, token = self._lease_active(key=_plan_runtime_key(request.plan))
        if runtime is not None:
            try:
                return runtime.generate(request)
            finally:
                self._release_active(token)
        result = self.fallback.generate(request)
        reported_model = str(getattr(result, "model", "") or "").strip()
        allowed_models = [request.plan.model]
        standby_model = str(request.plan.standby_model or "").strip()
        if standby_model:
            allowed_models.append(standby_model)
        if not any(
            _same_model_identity(reported_model, expected)
            for expected in allowed_models
        ):
            raise RuntimeError("Fallback AI returned the wrong model identity.")
        return result

    def status(self, model: str, *, timeout: float = 1.0) -> RuntimeStatus:
        with self._prepare_lock:
            runtime, token = self._lease_active(model=model)
            failure: Exception | None = None
            if runtime is not None:
                try:
                    return runtime.status(model, timeout=timeout)
                except (OSError, RuntimeError, ValueError) as exc:
                    failure = exc
                finally:
                    self._release_active(token)
                reason = str(failure or "Bundled AI runtime status failed.")[:300]
                detached_key = None
                with self._state:
                    if self._active is runtime:
                        detached_key = self._active_key
                        self._active = None
                        self._active_key = None
                if detached_key is not None:
                    self.last_downgrade_reason = reason
                    self._prepare_failures[detached_key] = time.monotonic()
                    self._prepare_failure_reasons[detached_key] = reason
                    self._retire(runtime)
            return self.fallback.status(model, timeout=timeout)

    def acceptance_probe(self, plan) -> AcceptanceProbeResult:
        runtime, token = self._lease_active(key=_plan_runtime_key(plan))
        if runtime is not None:
            try:
                probe = runtime.acceptance_probe(plan)
            finally:
                self._release_active(token)
        else:
            probe = self.fallback.acceptance_probe(plan)

        latency_limit = max(0.0, float(plan.warm_latency_limit_seconds))
        latency_passed = (
            latency_limit == 0.0
            or probe.elapsed_seconds <= latency_limit
        )
        backend_passed = probe.backend is plan.backend
        identity_passed = (
            not plan.use_llm
            or _same_model_identity(probe.model_identity, plan.model)
        )
        accepted = (
            probe.accepted
            and probe.correctness_passed
            and probe.protected_passed
            and latency_passed
            and backend_passed
            and identity_passed
        )
        reasons = [value.strip() for value in (probe.reason,) if value.strip()]
        if not probe.correctness_passed:
            reasons.append("Correctness probe failed.")
        if not probe.protected_passed:
            reasons.append("Protected facts changed.")
        if not latency_passed:
            reasons.append(
                f"Median warm probe took {probe.elapsed_seconds:.2f}s; limit is "
                f"{latency_limit:.2f}s."
            )
        if not backend_passed:
            reasons.append("Backend identity did not match the plan.")
        if not identity_passed:
            reasons.append("Model identity was missing or did not match the plan.")
        if not probe.accepted and not reasons:
            reasons.append("Runtime probe rejected model.")
        return AcceptanceProbeResult(
            accepted=accepted,
            elapsed_seconds=probe.elapsed_seconds,
            correctness_passed=probe.correctness_passed,
            protected_passed=probe.protected_passed,
            backend=probe.backend,
            reason="; ".join(dict.fromkeys(reasons)),
            model_identity=probe.model_identity,
        )

    def close(self) -> None:
        with self._prepare_lock:
            with self._state:
                previous = self._active
                self._active = None
                self._active_key = None
            self._retire(previous)

    def _lease_active(
        self,
        *,
        key: tuple[str, str, str, int] | None = None,
        model: str | None = None,
    ) -> tuple[NamedPipeLlamaRuntime | None, int | None]:
        with self._state:
            runtime = self._active
            active_key = self._active_key
            if runtime is None or active_key is None:
                return None, None
            if key is not None and active_key != key:
                return None, None
            if model is not None and active_key[1] != str(model).strip():
                return None, None
            token = id(runtime)
            self._active_leases[token] = self._active_leases.get(token, 0) + 1
            return runtime, token

    def _release_active(self, token: int | None) -> None:
        if token is None:
            return
        with self._state:
            remaining = self._active_leases.get(token, 0) - 1
            if remaining > 0:
                self._active_leases[token] = remaining
            else:
                self._active_leases.pop(token, None)
                self._state.notify_all()

    def _retire(self, runtime: NamedPipeLlamaRuntime | None) -> None:
        if runtime is None:
            return
        token = id(runtime)
        deadline = time.monotonic() + max(0.0, self._retire_timeout_seconds)
        with self._state:
            while self._active_leases.get(token, 0) > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._state.wait(timeout=remaining)
        runtime.close()


def _read_exact(handle, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        value = handle.read(remaining)
        if not value:
            raise RuntimeError("llama.cpp host closed the pipe early.")
        chunks.append(value)
        remaining -= len(value)
    return b"".join(chunks)


def _wait_named_pipe(pipe_name: str, timeout: float) -> None:
    if sys.platform != "win32":
        return
    deadline = time.monotonic() + max(0.001, min(30.0, float(timeout)))
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        wait_named_pipe = kernel32.WaitNamedPipeW
        wait_named_pipe.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
        wait_named_pipe.restype = ctypes.c_int
    except (AttributeError, OSError) as exc:
        raise RuntimeError(f"Windows named-pipe wait failed: {exc}") from exc
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            error = ctypes.get_last_error()
            raise RuntimeError(
                "Bundled llama.cpp host did not become ready "
                f"(Windows error {error})."
            )
        milliseconds = max(1, min(30_000, int(remaining * 1000)))
        if wait_named_pipe(pipe_name, milliseconds):
            return
        error = ctypes.get_last_error()
        if error not in {2, 121, 231}:
            raise RuntimeError(
                "Bundled llama.cpp host pipe failed "
                f"(Windows error {error})."
            )
        time.sleep(min(0.05, max(0.001, remaining)))


def _os_file_handle(pipe) -> int:
    import msvcrt

    return int(msvcrt.get_osfhandle(pipe.fileno()))


def _cancel_io(handle: int) -> None:
    if sys.platform != "win32":
        return
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        cancel_io = kernel32.CancelIoEx
        cancel_io.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        cancel_io.restype = ctypes.c_int
        cancel_io(ctypes.c_void_p(handle), None)
    except (AttributeError, OSError):
        return


def _host_failure_detail(process: subprocess.Popen) -> str:
    stream = getattr(process, "stderr", None)
    if stream is None:
        return ""
    try:
        value = str(stream.read() or "").strip()
    except (OSError, ValueError):
        return ""
    value = " ".join(value.split())
    return value[-500:]


__all__ = [
    "AdaptiveInferenceRuntime",
    "NamedPipeLlamaRuntime",
    "OllamaInferenceRuntime",
]
