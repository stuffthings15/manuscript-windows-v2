from __future__ import annotations

import hashlib
import copy
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from echo_link_core.paths import resource_path
from echo_link_core.polish_model_packs import (
    ModelPackConsentRequired,
    ModelPackDownloadError,
    ModelPackManager,
    ModelPackSecurityError,
)
from echo_link_core.polish_types import (
    ExecutionPlan,
    PolishMode,
    PolishModelPack,
    PolishTask,
    RuntimeBackend,
)


QWEN14_DIGEST = "bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8"


class FakeWritingModels:
    ollama_executable = None

    def __init__(self, values=()):
        self.values = tuple(values)
        self.removed = []

    def list_models(self, *, timeout=2.0):
        return self.values

    def remove_model(self, name):
        self.removed.append(name)


def local_qwen14(*, digest=QWEN14_DIGEST, quantization="Q4_K_M"):
    return SimpleNamespace(
        name="qwen3:14b",
        digest=digest,
        size_bytes=9_276_198_565,
        quantization=quantization,
    )


def manager(tmp_path, *, models=()):
    return ModelPackManager(
        install_root=tmp_path / "packs",
        writing_models=FakeWritingModels(models),
    )


def plan(pack_id):
    return ExecutionPlan(
        policy_version=1,
        mode=PolishMode.AUTO,
        task=PolishTask.PROOFREAD,
        use_llm=True,
        pack_id=pack_id,
        model="qwen3:14b",
        backend=RuntimeBackend.CUDA,
    )


def test_bundled_manifest_signature_and_catalog_are_valid(tmp_path):
    packs = manager(tmp_path, models=(local_qwen14(),)).catalog()
    assert [value.pack_id for value in packs] == [
        "qwen3-1.7b-q4-k-m-v1",
        "qwen3-4b-q4-k-m-v1",
        "qwen3-8b-q4-k-m-v1",
        "qwen3-14b-q4-k-m-v1",
        "gemma4-12b-q4-k-m-v1",
    ]
    qwen14 = next(
        value for value in packs if value.pack_id == "qwen3-14b-q4-k-m-v1"
    )
    assert qwen14.installed
    assert qwen14.validated
    assert qwen14.manifest_digest == QWEN14_DIGEST


def test_wrong_local_digest_or_quantization_is_not_installed(tmp_path):
    wrong_digest = manager(
        tmp_path / "digest",
        models=(local_qwen14(digest="0" * 64),),
    ).catalog()
    wrong_digest = next(
        value for value in wrong_digest if value.pack_id == "qwen3-14b-q4-k-m-v1"
    )
    wrong_quant = manager(
        tmp_path / "quant",
        models=(local_qwen14(quantization="Q5_K_M"),),
    ).catalog()
    wrong_quant = next(
        value for value in wrong_quant if value.pack_id == "qwen3-14b-q4-k-m-v1"
    )
    assert not wrong_digest.installed
    assert not wrong_quant.installed


def test_tampered_manifest_fails_closed(tmp_path):
    source = resource_path("resources", "polish")
    target = tmp_path / "signed"
    shutil.copytree(source, target)
    manifest = target / "model-packs.json"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace('"14.8B"', '"99B"'),
        encoding="utf-8",
    )
    value = ModelPackManager(
        manifest_path=manifest,
        signature_path=target / "model-packs.sig",
        public_key_path=target / "model-packs-public-key.json",
        install_root=tmp_path / "packs",
        writing_models=FakeWritingModels(),
    )
    with pytest.raises(ModelPackSecurityError, match="signature"):
        value.catalog()


def test_tampered_public_key_fails_pinned_fingerprint(tmp_path):
    source = resource_path("resources", "polish")
    target = tmp_path / "signed"
    shutil.copytree(source, target)
    public_key = target / "model-packs-public-key.json"
    public_key.write_text(
        public_key.read_text(encoding="utf-8").replace(
            '"exponent":  65537',
            '"exponent":  3',
        ),
        encoding="utf-8",
    )
    value = ModelPackManager(
        manifest_path=target / "model-packs.json",
        signature_path=target / "model-packs.sig",
        public_key_path=public_key,
        install_root=tmp_path / "packs",
        writing_models=FakeWritingModels(),
    )
    with pytest.raises(ModelPackSecurityError, match="public key changed"):
        value.catalog()


def test_uninstalled_pack_requires_explicit_consent(tmp_path):
    value = manager(tmp_path)
    with pytest.raises(ModelPackConsentRequired) as error:
        value.ensure(plan("qwen3-8b-q4-k-m-v1"), consent=False)
    assert error.value.pack.size_bytes == 5_225_387_677


def test_exact_installed_pack_needs_no_download_consent(tmp_path):
    value = manager(tmp_path, models=(local_qwen14(),))
    pack = value.ensure(plan("qwen3-14b-q4-k-m-v1"), consent=False)
    assert pack.installed


def test_pack_removal_is_explicit(tmp_path):
    writing = FakeWritingModels((local_qwen14(),))
    value = ModelPackManager(
        install_root=tmp_path / "packs",
        writing_models=writing,
    )
    value.remove("qwen3-14b-q4-k-m-v1")
    assert writing.removed == ["qwen3:14b"]


def test_signed_manifest_pack_id_migration(tmp_path):
    value = manager(tmp_path)
    manifest = copy.deepcopy(value._load_verified_manifest())
    manifest["packs"][0]["replaces"] = ["qwen3-1.7b-old"]
    value._verified_manifest = manifest
    assert (
        value.migrate_pack_id("qwen3-1.7b-old")
        == "qwen3-1.7b-q4-k-m-v1"
    )
    assert value.migrate_pack_id("unknown-pack") == "unknown-pack"


class FakeResponse:
    def __init__(self, content: bytes, *, declared_size: int):
        self.content = content
        self.offset = 0
        self.headers = {"Content-Length": str(declared_size)}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def geturl(self):
        return "https://models.example.test/model.gguf"

    def read(self, size):
        value = self.content[self.offset : self.offset + size]
        self.offset += len(value)
        return value


class FakeOpener:
    def __init__(self, response):
        self.response = response

    def open(self, request, timeout):
        return self.response


def file_pack(content: bytes) -> PolishModelPack:
    return PolishModelPack(
        pack_id="test-file-pack",
        display_name="Test file pack",
        model="test",
        family="Qwen3",
        parameters="test",
        quantization="Q4_K_M",
        runtime="llama.cpp",
        manifest_digest=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        min_ram_bytes=1,
        min_vram_bytes=0,
        max_context_tokens=4096,
        supported_backends=(RuntimeBackend.CPU,),
        license_id="Apache-2.0",
        source_url="https://example.test",
        download_ref="https://models.example.test/model.gguf",
    )


def test_interrupted_download_removes_partial_file(tmp_path, monkeypatch):
    pack = file_pack(b"complete model bytes")
    response = FakeResponse(b"short", declared_size=pack.size_bytes)
    monkeypatch.setattr(
        "echo_link_core.polish_model_packs.urllib.request.build_opener",
        lambda *_: FakeOpener(response),
    )
    monkeypatch.setattr(
        "echo_link_core.polish_model_packs.shutil.disk_usage",
        lambda *_: SimpleNamespace(free=10 * 1024**3),
    )
    value = manager(tmp_path)
    with pytest.raises(ModelPackSecurityError, match="incomplete"):
        value._download_file(pack, progress=None)
    assert not (tmp_path / "packs" / pack.pack_id / "model.gguf.partial").exists()


def test_corrupted_download_fails_sha256_and_is_not_installed(tmp_path, monkeypatch):
    pack = file_pack(b"expected model")
    corrupt = b"corrupt model!"
    assert len(corrupt) == pack.size_bytes
    response = FakeResponse(corrupt, declared_size=pack.size_bytes)
    monkeypatch.setattr(
        "echo_link_core.polish_model_packs.urllib.request.build_opener",
        lambda *_: FakeOpener(response),
    )
    monkeypatch.setattr(
        "echo_link_core.polish_model_packs.shutil.disk_usage",
        lambda *_: SimpleNamespace(free=10 * 1024**3),
    )
    value = manager(tmp_path)
    with pytest.raises(ModelPackSecurityError, match="SHA-256"):
        value._download_file(pack, progress=None)
    assert not (tmp_path / "packs" / pack.pack_id / "model.gguf").exists()


def test_disk_space_failure_happens_before_network(tmp_path, monkeypatch):
    pack = file_pack(b"expected model")
    monkeypatch.setattr(
        "echo_link_core.polish_model_packs.shutil.disk_usage",
        lambda *_: SimpleNamespace(free=1),
    )
    value = manager(tmp_path)
    with pytest.raises(ModelPackDownloadError, match="disk space"):
        value._download_file(pack, progress=None)


def test_direct_llama_pack_returns_only_a_real_local_gguf(tmp_path):
    content = b"GGUF" + b"local model"
    pack = file_pack(content)
    value = manager(tmp_path)
    value._verified_manifest = {
        "schema_version": 1,
        "policy_version": 1,
        "packs": [
            {
                "id": pack.pack_id,
                "display_name": pack.display_name,
                "model": pack.model,
                "family": pack.family,
                "parameters": pack.parameters,
                "quantization": pack.quantization,
                "runtime": pack.runtime,
                "manifest_digest": pack.manifest_digest,
                "size_bytes": pack.size_bytes,
                "min_ram_bytes": pack.min_ram_bytes,
                "min_vram_bytes": pack.min_vram_bytes,
                "max_context_tokens": pack.max_context_tokens,
                "backends": [value.value for value in pack.supported_backends],
                "license": pack.license_id,
                "source_url": pack.source_url,
                "download_ref": pack.download_ref,
                "validated": True,
            }
        ],
    }
    target = tmp_path / "packs" / pack.pack_id / "model.gguf"
    target.parent.mkdir(parents=True)
    target.write_bytes(content)
    assert value.model_file(pack.pack_id) == target.resolve()

    target.write_bytes(b"NOPE" + b"local model")
    value._model_files.clear()
    with pytest.raises(ModelPackSecurityError, match="GGUF"):
        value.model_file(pack.pack_id)
