from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .data_protection import UnsafePathError, validate_local_data_file
from .local_transport import LocalTransportError, LoopbackJSONTransport
from .models import OLLAMA_KEEP_ALIVE

_MODEL_NAME = re.compile(
    r"[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)*(?::[a-z0-9][a-z0-9._-]*)?"
)
_BENCHMARK_TOKEN = "ECHOLINKBENCHMARK"


class WritingModelError(RuntimeError):
    """A safe, user-facing failure from the local writing-model workflow."""


@dataclass(frozen=True, slots=True)
class WritingModelInfo:
    name: str
    digest: str
    size_bytes: int
    modified_at: str
    format: str
    family: str
    parameter_size: str
    quantization: str

    @property
    def version(self) -> str:
        return self.name.rsplit(":", 1)[1] if ":" in self.name else "latest"


@dataclass(frozen=True, slots=True)
class WritingModelImport:
    model: WritingModelInfo
    source_path: str
    source_size_bytes: int
    source_sha256: str


@dataclass(frozen=True, slots=True)
class WritingModelBenchmark:
    model: str
    elapsed_seconds: float
    load_seconds: float
    evaluation_seconds: float
    output_tokens: int
    tokens_per_second: float
    response_verified: bool


class LocalWritingModelManager:
    """Manage Ollama writing models through loopback-only local interfaces."""

    def __init__(
        self,
        endpoint: str = "http://127.0.0.1:11434",
        *,
        ollama_executable: str | Path | None = None,
    ) -> None:
        self._transport = LoopbackJSONTransport(endpoint)
        self.ollama_executable = str(ollama_executable) if ollama_executable else None

    @property
    def endpoint(self) -> str:
        return self._transport.endpoint

    def server_version(self, *, timeout: float = 2.0) -> str:
        payload = self._request_json("/api/version", timeout=timeout)
        return str(payload.get("version") or "unknown")

    def ensure_running(self, *, timeout: float = 8.0) -> str:
        """Start the installed local Ollama service when it is not already running."""
        try:
            return self.server_version(timeout=min(0.75, max(0.2, float(timeout))))
        except WritingModelError:
            pass
        executable = self._find_executable()
        creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            subprocess.Popen(
                [executable, "serve"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise WritingModelError(f"Local Ollama service could not start: {exc}") from exc
        deadline = time.monotonic() + max(1.0, float(timeout))
        last_error: WritingModelError | None = None
        while time.monotonic() < deadline:
            try:
                return self.server_version(timeout=0.5)
            except WritingModelError as exc:
                last_error = exc
                time.sleep(0.1)
        detail = str(last_error) if last_error is not None else "startup timed out"
        raise WritingModelError(f"Local Ollama service could not start: {detail}")

    def list_models(self, *, timeout: float = 3.0) -> tuple[WritingModelInfo, ...]:
        payload = self._request_json("/api/tags", timeout=timeout)
        models: list[WritingModelInfo] = []
        for raw in payload.get("models", []):
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or raw.get("model") or "").strip()
            if not name:
                continue
            details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
            models.append(
                WritingModelInfo(
                    name=name,
                    digest=str(raw.get("digest") or ""),
                    size_bytes=max(0, int(raw.get("size") or 0)),
                    modified_at=str(raw.get("modified_at") or ""),
                    format=str(details.get("format") or ""),
                    family=str(details.get("family") or ""),
                    parameter_size=str(details.get("parameter_size") or ""),
                    quantization=str(details.get("quantization_level") or ""),
                )
            )
        return tuple(sorted(models, key=lambda item: item.name.casefold()))

    def import_gguf(
        self,
        source: str | Path,
        *,
        name: str,
        timeout: float = 1800.0,
    ) -> WritingModelImport:
        model_name = _normalize_model_name(name)
        path = _validate_gguf(source)
        executable = self._find_executable()
        source_size = path.stat().st_size
        source_sha256 = _sha256(path)
        with tempfile.TemporaryDirectory(prefix="echo-link-writing-model-") as raw_temp:
            modelfile = Path(raw_temp) / "Modelfile"
            quoted_path = path.as_posix().replace('"', '\\"')
            modelfile.write_text(f'FROM "{quoted_path}"\n', encoding="utf-8")
            try:
                completed = subprocess.run(
                    [executable, "create", model_name, "-f", str(modelfile)],
                    cwd=raw_temp,
                    capture_output=True,
                    text=True,
                    timeout=max(30.0, float(timeout)),
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise WritingModelError(f"Local writing-model import failed: {exc}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "Ollama returned an error.").strip()
            raise WritingModelError(f"Local writing-model import failed: {detail}")
        model = _find_model(self.list_models(timeout=10.0), model_name)
        if model is None:
            raise WritingModelError("Ollama completed the import, but the local model was not listed.")
        return WritingModelImport(
            model=model,
            source_path=str(path),
            source_size_bytes=source_size,
            source_sha256=source_sha256,
        )

    def remove_model(self, name: str, *, timeout: float = 120.0) -> None:
        model_name = _normalize_model_name(name)
        executable = self._find_executable()
        try:
            completed = subprocess.run(
                [executable, "rm", model_name],
                capture_output=True,
                text=True,
                timeout=max(10.0, float(timeout)),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise WritingModelError(f"Local writing-model removal failed: {exc}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "Ollama returned an error.").strip()
            raise WritingModelError(f"Local writing-model removal failed: {detail}")

    def benchmark(self, name: str, *, timeout: float = 120.0) -> WritingModelBenchmark:
        model_name = _normalize_model_name(name)
        started = time.perf_counter()
        payload = self._request_json(
            "/api/generate",
            method="POST",
            data={
                "model": model_name,
                "prompt": f"Return exactly {_BENCHMARK_TOKEN} and nothing else.",
                "stream": False,
                "think": False,
                "keep_alive": OLLAMA_KEEP_ALIVE,
                "options": {"temperature": 0, "num_predict": 16},
            },
            timeout=max(5.0, float(timeout)),
        )
        elapsed = max(0.0, time.perf_counter() - started)
        evaluation_seconds = max(0.0, float(payload.get("eval_duration") or 0) / 1_000_000_000)
        load_seconds = max(0.0, float(payload.get("load_duration") or 0) / 1_000_000_000)
        output_tokens = max(0, int(payload.get("eval_count") or 0))
        tokens_per_second = output_tokens / evaluation_seconds if evaluation_seconds else 0.0
        response = str(payload.get("response") or "").strip()
        return WritingModelBenchmark(
            model=model_name,
            elapsed_seconds=elapsed,
            load_seconds=load_seconds,
            evaluation_seconds=evaluation_seconds,
            output_tokens=output_tokens,
            tokens_per_second=tokens_per_second,
            response_verified=response == _BENCHMARK_TOKEN,
        )

    def _find_executable(self) -> str:
        candidate = self.ollama_executable or shutil.which("ollama") or shutil.which("ollama.exe")
        if not candidate:
            raise WritingModelError("Ollama is not installed or is not available on PATH.")
        return str(candidate)

    def _request_json(
        self,
        path: str,
        *,
        method: str = "GET",
        data: dict | None = None,
        timeout: float,
    ) -> dict:
        try:
            if method == "GET":
                payload = self._transport.get_json(
                    path,
                    timeout=max(0.2, float(timeout)),
                )
            elif method == "POST" and data is not None:
                payload = self._transport.post_json(
                    path,
                    data,
                    timeout=max(0.2, float(timeout)),
                )
            else:
                raise LocalTransportError("Local Ollama request is invalid.")
        except LocalTransportError as exc:
            raise WritingModelError(f"Local Ollama service unavailable: {exc}") from exc
        if not isinstance(payload, dict):
            raise WritingModelError("Local Ollama service returned an invalid response.")
        if payload.get("error"):
            raise WritingModelError(f"Local Ollama error: {payload['error']}")
        return payload
def _normalize_model_name(name: str) -> str:
    value = (name or "").strip().casefold()
    if not _MODEL_NAME.fullmatch(value):
        raise ValueError("Use a local model name such as echo-link-writing:latest.")
    return value


def _validate_gguf(source: str | Path) -> Path:
    try:
        path = validate_local_data_file(
            source,
            purpose="GGUF model",
            allowed_suffixes={".gguf"},
        )
    except UnsafePathError as exc:
        raise WritingModelError(str(exc)) from exc
    except (FileNotFoundError, OSError, RuntimeError) as exc:
        raise WritingModelError("The local GGUF file was not found.") from exc
    if not path.is_file():
        raise WritingModelError("Choose one local .gguf model file.")
    try:
        with path.open("rb") as stream:
            magic = stream.read(4)
    except OSError as exc:
        raise WritingModelError(f"The local GGUF file could not be read: {exc}") from exc
    if magic != b"GGUF":
        raise WritingModelError("The selected file is not a valid GGUF model.")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise WritingModelError(f"The local GGUF file could not be hashed: {exc}") from exc
    return digest.hexdigest().upper()


def _find_model(models: tuple[WritingModelInfo, ...], name: str) -> WritingModelInfo | None:
    base = name.split(":", 1)[0]
    return next(
        (
            model
            for model in models
            if model.name.casefold() == name
            or (":" not in name and model.name.casefold().split(":", 1)[0] == base)
        ),
        None,
    )
