from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import zipfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from echo_link_core.paths import resource_path
from echo_link_core.polish_runtime_packs import (
    PolishRuntimePack,
    RuntimePackConsentRequired,
    RuntimePackManager,
    RuntimePackSecurityError,
    _safe_extract_zip,
)
from echo_link_core.polish_types import RuntimeBackend


def manager(tmp_path: Path, **kwargs) -> RuntimePackManager:
    return RuntimePackManager(install_root=tmp_path / "runtime-packs", **kwargs)


def test_signed_runtime_manifest_has_every_backend_and_exact_release(tmp_path):
    packs = manager(tmp_path).catalog(refresh_installed=False)
    assert {value.backend for value in packs} == {
        RuntimeBackend.CPU,
        RuntimeBackend.CUDA,
        RuntimeBackend.VULKAN,
        RuntimeBackend.HIP,
        RuntimeBackend.SYCL,
    }
    assert {value.version for value in packs} == {"b10103"}
    assert len([value for value in packs if value.backend is RuntimeBackend.CUDA]) == 2
    assert all(
        component.url.startswith(
            "https://github.com/ggml-org/llama.cpp/releases/download/b10103/"
        )
        for pack in packs
        for component in pack.components
    )


def test_tampered_runtime_manifest_fails_closed(tmp_path):
    source = resource_path("resources", "polish")
    target = tmp_path / "signed"
    shutil.copytree(source, target)
    manifest = target / "runtime-packs.json"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            '"llama.cpp b10103 CPU"',
            '"untrusted runtime"',
        ),
        encoding="utf-8",
    )
    value = RuntimePackManager(
        manifest_path=manifest,
        signature_path=target / "runtime-packs.sig",
        public_key_path=target / "model-packs-public-key.json",
        install_root=tmp_path / "packs",
    )
    with pytest.raises(RuntimePackSecurityError, match="signature"):
        value.catalog()


def test_cuda_candidate_uses_measured_cuda_runtime_version(tmp_path, monkeypatch):
    value = manager(tmp_path)
    monkeypatch.setattr(
        "echo_link_core.polish_runtime_packs._cuda_umd_major",
        lambda: 13,
    )
    assert "cuda-13.3" in value.select(RuntimeBackend.CUDA).pack_id
    monkeypatch.setattr(
        "echo_link_core.polish_runtime_packs._cuda_umd_major",
        lambda: 12,
    )
    assert "cuda-12.4" in value.select(RuntimeBackend.CUDA).pack_id


def test_runtime_download_requires_explicit_consent(tmp_path):
    value = manager(tmp_path)
    with pytest.raises(RuntimePackConsentRequired) as error:
        value.ensure(RuntimeBackend.CPU, consent=False)
    assert error.value.pack.download_size_bytes > 0
    assert error.value.pack.display_name not in str(error.value)


def test_zip_traversal_and_links_are_rejected(tmp_path):
    traversal = tmp_path / "traversal.zip"
    with zipfile.ZipFile(traversal, "w") as bundle:
        bundle.writestr("../outside.exe", b"bad")
    with pytest.raises(RuntimePackSecurityError, match="unsafe"):
        _safe_extract_zip(traversal, tmp_path / "target")

    linked = tmp_path / "link.zip"
    info = zipfile.ZipInfo("llama-server.exe")
    info.create_system = 3
    info.external_attr = (0o120777 << 16)
    with zipfile.ZipFile(linked, "w") as bundle:
        bundle.writestr(info, b"target")
    with pytest.raises(RuntimePackSecurityError, match="link"):
        _safe_extract_zip(linked, tmp_path / "target-link")


def test_runtime_install_receipt_detects_later_corruption(tmp_path, monkeypatch):
    archive = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("llama-server.exe", b"MZ" + b"\0" * 64)
        bundle.writestr("ggml.dll", b"runtime")
    archive_bytes = archive.read_bytes()
    component = SimpleNamespace(
        filename="runtime.zip",
        url="https://example.test/runtime.zip",
        size_bytes=len(archive_bytes),
        sha256=hashlib.sha256(archive_bytes).hexdigest(),
    )
    pack = PolishRuntimePack(
        pack_id="test-runtime",
        display_name="Test runtime",
        backend=RuntimeBackend.CPU,
        version="1",
        architecture="x64",
        entry_point="llama-server.exe",
        components=(component,),
        license_id="MIT",
        source_url="https://example.test",
    )
    value = manager(tmp_path)

    def fake_download(_component, target, *, progress):
        shutil.copyfile(archive, target)

    monkeypatch.setattr(value, "_download_component", fake_download)
    value._install(pack, progress=None)
    installed = value._installed_state(pack)
    assert installed.installed
    (Path(installed.install_path) / "ggml.dll").write_bytes(b"corrupt")
    assert not value._installed_state(pack).installed


def test_backend_probe_must_report_the_requested_compute_api(tmp_path):
    target = tmp_path / "cuda"
    target.mkdir()
    executable = target / "llama-server.exe"
    executable.write_bytes(b"MZ")
    pack = PolishRuntimePack(
        pack_id="cuda-probe",
        display_name="CUDA probe",
        backend=RuntimeBackend.CUDA,
        version="1",
        architecture="x64",
        entry_point="llama-server.exe",
        components=(),
        license_id="MIT",
        source_url="https://example.test",
        installed=True,
        install_path=str(target),
    )
    success = RuntimePackManager(
        install_root=tmp_path / "packs",
        runner=lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, "CUDA0: NVIDIA RTX", ""
        ),
    )
    assert "CUDA0" in success.probe(pack).probe_detail

    failed = RuntimePackManager(
        install_root=tmp_path / "packs",
        runner=lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, "CPU only", ""
        ),
    )
    with pytest.raises(RuntimeError, match="no matching"):
        failed.probe(pack)


def test_native_host_source_enforces_pipe_auth_and_private_llama_server():
    source = (
        Path(__file__).parents[1] / "native" / "polish-host" / "Program.cs"
    ).read_text(encoding="utf-8")
    assert "PipeOptions.CurrentUserOnly" in source
    assert "CryptographicOperations.FixedTimeEquals" in source
    assert "MANUSCRIPT_POLISH_PIPE_SECRET" in source
    assert 'start.Environment["LLAMA_API_KEY"]' in source
    assert '"--api-key"' not in source
    assert '["model"] = configuration.ModelName' in source
    assert '["full_gpu_resident"] = state.FullGpuResident' in source
    assert '"--no-ui"' in source
    assert '"--sleep-idle-seconds"' in source
    assert "full GPU residency" in source
    assert "RedirectStandardOutput = true" in source
    assert "FileAttributes.ReparsePoint" in source
    assert "Polish host path is not local." in source
    assert "TEXT:" not in source.split("CaptureSafeLog", 1)[1]
