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
    AcceptanceProbeResult,
    GenerationRequest,
    GenerationResult,
    RuntimeBackend,
    RuntimeStatus,
)
from .selected_text import LocalWritingAssistant
from .writing_models import LocalWritingModelManager


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
                ):
                    raise
                # The main model is still not answering, most often because it
                # is loading from cold or another application has taken the
                # card. The standby is small and already resident, so it can
                # correct this section now rather than leaving the reader with
                # rules-only text and "full AI correction did not run".
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
                    # Wherever it fits. The point is an answer, not placement.
                    require_gpu=False,
                    defer_meaning_validation=True,
                )
        elapsed = max(0.0, time.perf_counter() - started)
        status = self.status(request.plan.model, timeout=1.0)
        return GenerationResult(
            text=result.text,
            model=result.model,
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
            model=value.model,
            loaded=value.loaded,
            backend=backend,
            model_bytes=value.model_bytes,
            gpu_bytes=value.gpu_bytes,
            detail=value.backend,
        )

    def acceptance_probe(self, plan) -> AcceptanceProbeResult:
        if not plan.use_llm:
            return AcceptanceProbeResult(
                accepted=True,
                elapsed_seconds=0.0,
                correctness_passed=True,
                protected_passed=True,
                backend=RuntimeBackend.RULES,
                reason="Rules-only plan.",
            )
        try:
            if plan.require_full_gpu:
                self.assistant.require_gpu(
                    plan.model,
                    timeout=plan.cold_load_timeout_seconds,
                    keep_alive=OLLAMA_KEEP_ALIVE,
                    context_window=min(2048, plan.context_window),
                )
            if plan.standby_model:
                # Load the standby now, while nobody is waiting on a
                # correction. A standby that has to load at the moment the main
                # model stalls is no faster than the stall it is covering.
                try:
                    self.assistant.require_gpu(
                        plan.standby_model,
                        timeout=plan.cold_load_timeout_seconds,
                        keep_alive=OLLAMA_KEEP_ALIVE,
                        context_window=min(2048, plan.context_window),
                    )
                except Exception:  # noqa: BLE001
                    # The load is the point; where it landed is not. A standby
                    # that ended up on the CPU still answers, and a standby
                    # that failed entirely simply leaves today's behaviour.
                    pass
            source = (
                "The report are not ready, and it fail. Keep build 47 at "
                "http://127.0.0.1:7777/protected until July 23, 2026."
            )
            elapsed_samples: list[float] = []
            correctness_samples: list[bool] = []
            protected_samples: list[bool] = []
            for _sample in range(3):
                started = time.perf_counter()
                result = self.assistant.run(
                    source,
                    command="Proofread",
                    model=plan.model,
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
            status = self.status(plan.model, timeout=1.0)
            backend_ok = (
                not plan.require_full_gpu
                or status.backend
                in {
                    RuntimeBackend.CUDA,
                    RuntimeBackend.HIP,
                    RuntimeBackend.VULKAN,
                    RuntimeBackend.SYCL,
                }
            )
            latency_ok = elapsed <= plan.warm_latency_limit_seconds
            # Correctness and latency rank packs; they no longer decide whether
            # the local AI may run at all. The correctness probe demands three
            # literal phrases, so a model that answers "the report has not been
            # completed and it failed" - correct English, correct meaning -
            # scored as broken and turned the AI off for the whole session.
            # Protected facts and GPU residency stay decisive because those are
            # about the model damaging text or the machine, not its phrasing.
            accepted = protected and backend_ok
            reasons: list[str] = []
            if not correctness:
                reasons.append("correctness probe failed")
            if not protected:
                reasons.append("protected facts changed")
            if not backend_ok:
                reasons.append("full GPU residency failed")
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
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return AcceptanceProbeResult(
                accepted=False,
                elapsed_seconds=0.0,
                correctness_passed=False,
                protected_passed=False,
                backend=RuntimeBackend.RULES,
                reason=str(exc)[:300],
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
        model_name: str = "",
        backend: RuntimeBackend = RuntimeBackend.CPU,
        context_window: int = 4096,
        idle_seconds: int = 300,
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
        self.model_name = str(model_name).strip()
        self.backend = RuntimeBackend(backend)
        self.context_window = max(512, min(32_768, int(context_window)))
        self.idle_seconds = max(60, min(3600, int(idle_seconds)))
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
        return "llama.cpp:named-pipe:v2"

    def hello(self, *, timeout: float = 5.0) -> dict:
        return self._exchange({"type": "hello"}, timeout=timeout)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        started = time.perf_counter()
        payload = self._exchange(
            {
                "type": "generate",
                "model_pack_id": request.plan.pack_id,
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
        return GenerationResult(
            text=payload["text"],
            model=str(payload.get("model") or request.plan.model),
            backend=RuntimeBackend(str(payload.get("backend") or request.plan.backend.value)),
            elapsed_seconds=max(0.0, time.perf_counter() - started),
            input_tokens=max(0, int(payload.get("input_tokens") or 0)),
            output_tokens=max(0, int(payload.get("output_tokens") or 0)),
        )

    def status(self, model: str, *, timeout: float = 1.0) -> RuntimeStatus:
        payload = self._exchange(
            {
                "type": "status",
                "model": model,
                "timeout_seconds": max(0.2, min(5.0, float(timeout))),
            },
            timeout=timeout,
        )
        backend = RuntimeBackend(str(payload.get("backend") or "rules"))
        return RuntimeStatus(
            model=str(payload.get("model") or model),
            loaded=bool(payload.get("loaded")),
            backend=backend,
            model_bytes=max(0, int(payload.get("model_bytes") or 0)),
            gpu_bytes=max(0, int(payload.get("gpu_bytes") or 0)),
            detail=str(payload.get("detail") or ""),
        )

    def acceptance_probe(self, plan) -> AcceptanceProbeResult:
        payload = self._exchange(
            {
                "type": "acceptance_probe",
                "model_pack_id": plan.pack_id,
                "backend": plan.backend.value,
                "latency_limit_seconds": plan.warm_latency_limit_seconds,
            },
            timeout=plan.absolute_timeout_seconds,
        )
        return AcceptanceProbeResult(
            accepted=bool(payload.get("accepted")),
            elapsed_seconds=max(0.0, float(payload.get("elapsed_seconds") or 0.0)),
            correctness_passed=bool(payload.get("correctness_passed")),
            protected_passed=bool(payload.get("protected_passed")),
            backend=RuntimeBackend(str(payload.get("backend") or "rules")),
            reason=str(payload.get("reason") or ""),
        )

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
        self._active_key: tuple[str, str, int] | None = None
        self._prepare_failures: dict[tuple[str, str, int], float] = {}
        self._prepare_failure_reasons: dict[tuple[str, str, int], str] = {}
        self.last_downgrade_reason = ""

    @property
    def runtime_id(self) -> str:
        return "adaptive:llama.cpp-named-pipe-v2:ollama-rollback"

    @property
    def parallel_slots(self) -> int:
        """How many corrections this runtime can genuinely run at once.

        The bundled llama.cpp host is ours and is configured with parallel
        slots. The Ollama rollback serializes large requests inside the
        server, so issuing several at once only multiplies each one's
        latency until they hit the request timeout - measured, not assumed.
        """
        if self._active is not None and self._active_key is not None:
            return 3
        return 1

    def prepare(
        self,
        plan,
        *,
        consent: bool,
    ) -> bool:
        key = (plan.pack_id, plan.backend.value, int(plan.context_window))
        if self._active is not None and self._active_key == key:
            return True
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
        try:
            model_path = self.model_packs.model_file(plan.pack_id)
            runtime_pack = self.runtime_packs.ensure(
                plan.backend,
                consent=consent,
            )
            runtime_pack = self.runtime_packs.probe(runtime_pack)
            if not self.host_path.is_file():
                raise RuntimeError("Bundled Polish host is not installed.")
            runtime = NamedPipeLlamaRuntime(
                host_path=self.host_path,
                server_path=runtime_pack.executable_path,
                model_path=model_path,
                model_name=plan.model,
                backend=plan.backend,
                context_window=plan.context_window,
                idle_seconds=300,
            )
            hello = runtime.hello(timeout=5.0)
            if hello.get("ok") is not True:
                raise RuntimeError("Bundled Polish host handshake failed.")
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
            self.last_downgrade_reason = str(exc)[:300]
            self._prepare_failures[key] = time.monotonic()
            self._prepare_failure_reasons[key] = self.last_downgrade_reason
            return False
        if self._active is not None:
            self._active.close()
        self._active = runtime
        self._active_key = key
        self.last_downgrade_reason = ""
        self._prepare_failures.pop(key, None)
        self._prepare_failure_reasons.pop(key, None)
        return True

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if self._active is not None and self._active_key is not None:
            if self._active_key[0] == request.plan.pack_id:
                return self._active.generate(request)
        return self.fallback.generate(request)

    def status(self, model: str, *, timeout: float = 1.0) -> RuntimeStatus:
        if self._active is not None:
            try:
                return self._active.status(model, timeout=timeout)
            except (OSError, RuntimeError, ValueError):
                pass
        return self.fallback.status(model, timeout=timeout)

    def acceptance_probe(self, plan) -> AcceptanceProbeResult:
        if self._active is not None and self._active_key is not None:
            if self._active_key[0] == plan.pack_id:
                return self._active.acceptance_probe(plan)
        return self.fallback.acceptance_probe(plan)

    def close(self) -> None:
        if self._active is not None:
            self._active.close()
            self._active = None
            self._active_key = None


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
