from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from echo_link_core import data_protection as protection
import echo_link_core.writing_models as writing_models
from echo_link_core.writing_models import (
    LocalWritingModelManager,
    WritingModelError,
    WritingModelInfo,
)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=None):
        return json.dumps(self.payload).encode("utf-8")


def test_manager_rejects_non_loopback_endpoints():
    with pytest.raises(ValueError, match="loopback"):
        LocalWritingModelManager("https://models.example.com")
    with pytest.raises(ValueError, match="loopback"):
        LocalWritingModelManager("http://127.0.0.1:11434/api")


def test_list_models_exposes_tag_digest_and_details(monkeypatch):
    def fake_urlopen(request, timeout):
        assert request.full_url == "http://127.0.0.1:11434/api/tags"
        assert request.method == "GET"
        assert timeout == 3.0
        return _Response(
            {
                "models": [
                    {
                        "name": "qwen2.5:3b",
                        "modified_at": "2026-07-19T12:00:00Z",
                        "size": 2_000_000_000,
                        "digest": "abc123",
                        "details": {
                            "format": "gguf",
                            "family": "qwen2",
                            "parameter_size": "3B",
                            "quantization_level": "Q4_K_M",
                        },
                    }
                ]
            }
        )

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    model = LocalWritingModelManager().list_models()[0]
    assert model.name == "qwen2.5:3b"
    assert model.version == "3b"
    assert model.digest == "abc123"
    assert model.parameter_size == "3B"
    assert model.quantization == "Q4_K_M"


def test_manager_starts_local_ollama_when_service_is_not_running(monkeypatch):
    manager = LocalWritingModelManager(ollama_executable="C:/Ollama/ollama.exe")
    attempts = iter(
        (
            WritingModelError("offline"),
            WritingModelError("starting"),
            "0.12.0",
        )
    )
    calls = []

    def server_version(**_kwargs):
        result = next(attempts)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(manager, "server_version", server_version)
    monkeypatch.setattr(
        writing_models.subprocess,
        "Popen",
        lambda command, **kwargs: calls.append((command, kwargs)) or SimpleNamespace(),
    )
    monkeypatch.setattr(writing_models.time, "sleep", lambda _seconds: None)

    assert manager.ensure_running(timeout=2.0) == "0.12.0"
    assert calls[0][0] == ["C:/Ollama/ollama.exe", "serve"]
    assert "shell" not in calls[0][1]
    assert calls[0][1]["stdout"] is writing_models.subprocess.DEVNULL


def test_benchmark_is_local_bounded_and_reports_rate(monkeypatch):
    def fake_urlopen(request, timeout):
        assert request.full_url == "http://127.0.0.1:11434/api/generate"
        body = json.loads(request.data.decode("utf-8"))
        assert body["model"] == "qwen2.5:3b"
        assert body["stream"] is False
        assert body["keep_alive"] == "300s"
        assert body["think"] is False
        assert body["options"]["num_predict"] == 16
        return _Response(
            {
                "response": "ECHOLINKBENCHMARK",
                "load_duration": 500_000_000,
                "eval_duration": 2_000_000_000,
                "eval_count": 20,
            }
        )

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingModelManager().benchmark("qwen2.5:3b")
    assert result.load_seconds == 0.5
    assert result.evaluation_seconds == 2.0
    assert result.tokens_per_second == 10.0
    assert result.response_verified is True


def test_import_validates_hashes_and_uses_local_ollama_cli(monkeypatch, tmp_path: Path):
    source = tmp_path / "writing model.gguf"
    source.write_bytes(b"GGUF" + b"private-local-weights")
    expected_hash = hashlib.sha256(source.read_bytes()).hexdigest().upper()
    listed = WritingModelInfo(
        "echo-link-writing:latest",
        "ollama-digest",
        123,
        "now",
        "gguf",
        "qwen2",
        "3B",
        "Q4_K_M",
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        modelfile = Path(command[-1])
        assert modelfile.name == "Modelfile"
        assert source.as_posix() in modelfile.read_text(encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="success", stderr="")

    manager = LocalWritingModelManager(ollama_executable="C:/Ollama/ollama.exe")
    monkeypatch.setattr(writing_models.subprocess, "run", fake_run)
    monkeypatch.setattr(manager, "list_models", lambda **_kwargs: (listed,))

    result = manager.import_gguf(source, name="Echo-Link-Writing:Latest")
    assert calls[0][0][1:3] == ["create", "echo-link-writing:latest"]
    assert result.model.digest == "ollama-digest"
    assert result.source_path == str(source.resolve())
    assert result.source_sha256 == expected_hash
    assert result.source_size_bytes == source.stat().st_size


def test_import_rejects_fake_gguf_before_starting_ollama(tmp_path: Path):
    source = tmp_path / "fake.gguf"
    source.write_bytes(b"NOT-A-MODEL")
    manager = LocalWritingModelManager(ollama_executable="ollama")
    with pytest.raises(WritingModelError, match="not a valid GGUF"):
        manager.import_gguf(source, name="safe-model")


def test_import_rejects_unc_mapped_and_linked_gguf_before_ollama(
    monkeypatch,
    tmp_path: Path,
):
    manager = LocalWritingModelManager(ollama_executable="ollama")
    monkeypatch.setattr(
        writing_models.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("Unsafe GGUF reached Ollama."),
    )

    with pytest.raises(WritingModelError, match="Network"):
        manager.import_gguf(
            Path(r"\\server\share\private.gguf"),
            name="safe-model",
        )

    mapped = tmp_path / "mapped.gguf"
    mapped.write_bytes(b"GGUFprivate")
    original_network_check = protection.is_network_path
    monkeypatch.setattr(
        protection,
        "is_network_path",
        lambda value: Path(value).absolute() == mapped.absolute()
        or original_network_check(value),
    )
    with pytest.raises(WritingModelError, match="Network"):
        manager.import_gguf(mapped, name="safe-model")

    linked_parent = tmp_path / "linked-models"
    linked_parent.mkdir()
    linked = linked_parent / "private.gguf"
    linked.write_bytes(b"GGUFprivate")
    original_link_check = protection._is_link
    monkeypatch.setattr(protection, "is_network_path", original_network_check)
    monkeypatch.setattr(
        protection,
        "_is_link",
        lambda value: Path(value).absolute() == linked_parent.absolute()
        or original_link_check(value),
    )
    with pytest.raises(WritingModelError, match="Symbolic-link"):
        manager.import_gguf(linked, name="safe-model")


def test_remove_model_uses_validated_name(monkeypatch):
    calls = []
    monkeypatch.setattr(
        writing_models.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs))
        or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    manager = LocalWritingModelManager(ollama_executable="ollama")
    manager.remove_model("Private/Writer:Q4")
    assert calls[0][0] == ["ollama", "rm", "private/writer:q4"]
    with pytest.raises(ValueError):
        manager.remove_model("bad name && command")


def test_server_error_is_reported_without_remote_fallback(monkeypatch):
    monkeypatch.setattr(
        "echo_link_core.local_transport._open_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("offline")),
    )
    with pytest.raises(WritingModelError, match="Local Ollama service unavailable"):
        LocalWritingModelManager().list_models()
