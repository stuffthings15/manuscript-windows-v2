from __future__ import annotations

import hashlib
import copy
import shutil
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


GEMMA12_DIGEST = "4eb23ef187e2c5462566d6a1d3bbbc2f1346d0b4327cbb66d58fffbcc9b2b05c"


class FakeWritingModels:
    ollama_executable = None

    def __init__(self, values=()):
        self.values = tuple(values)
        self.removed = []

    def list_models(self, *, timeout=2.0):
        return self.values

    def remove_model(self, name):
        self.removed.append(name)


def local_gemma12(
    *,
    name="gemma4:12b-it-q4_K_M",
    digest=GEMMA12_DIGEST,
    size_bytes=7_556_508_396,
    quantization="Q4_K_M",
    family="gemma4",
):
    return SimpleNamespace(
        name=name,
        digest=digest,
        size_bytes=size_bytes,
        quantization=quantization,
        family=family,
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
        model="gemma4:12b-it-q4_K_M",
        backend=RuntimeBackend.CUDA,
    )


def test_bundled_manifest_signature_and_catalog_are_valid(tmp_path):
    packs = manager(tmp_path, models=(local_gemma12(),)).catalog()
    assert [value.pack_id for value in packs] == [
        "gemma4-e2b-qat-v1",
        "gemma4-e4b-qat-v1",
        "gemma4-12b-q4-k-m-v1",
        # Added 2026-08-24 on Curtis's instruction to move the product to
        # Qwen3.8. Both are in the signed manifest and neither is any tier's
        # default; they are here so they can be measured against the Gemma
        # packs on the same corpus before anything is promoted.
        "qwen3-14b-q4-k-m-v1",
        "qwen3dot8-27b-q4-k-m-v1",
    ]
    assert [value.model for value in packs] == [
        "gemma4:e2b-it-qat",
        "gemma4:e4b-it-qat",
        "gemma4:12b-it-q4_K_M",
        "qwen3:14b",
        "qwen3.8:27b",
    ]
    assert [value.size_bytes for value in packs] == [
        4_336_358_185,
        6_146_501_801,
        7_556_508_396,
        9_276_198_565,
        17_741_872_154,
    ]
    # This used to assert every pack was "Gemma 4" and that no model name
    # contained "qwen". Both were standing in for the real rule, which is that
    # the manifest may only carry models the tone path is allowed to run.
    # Tying it to the allowlist says that directly and keeps saying it when
    # the families change again - a family name and a substring would not have
    # noticed an unapproved model from a fourth vendor.
    from echo_link_core.selected_text import _OFFICIAL_MODELS

    assert all(value.model.casefold() in _OFFICIAL_MODELS for value in packs)
    gemma12 = next(
        value for value in packs if value.pack_id == "gemma4-12b-q4-k-m-v1"
    )
    assert gemma12.installed
    assert gemma12.validated
    assert gemma12.manifest_digest == GEMMA12_DIGEST


def test_wrong_local_digest_or_quantization_is_not_installed(tmp_path):
    wrong_digest = manager(
        tmp_path / "digest",
        models=(local_gemma12(digest="0" * 64),),
    ).catalog()
    wrong_digest = next(
        value for value in wrong_digest if value.pack_id == "gemma4-12b-q4-k-m-v1"
    )
    wrong_quant = manager(
        tmp_path / "quant",
        models=(local_gemma12(quantization="Q5_K_M"),),
    ).catalog()
    wrong_quant = next(
        value for value in wrong_quant if value.pack_id == "gemma4-12b-q4-k-m-v1"
    )
    assert not wrong_digest.installed
    assert not wrong_quant.installed


def test_exact_receipt_allows_safe_local_alias_and_returns_that_model(tmp_path):
    packs = manager(
        tmp_path,
        models=(local_gemma12(name="gemma4:12b"),),
    ).catalog()

    gemma12 = next(
        value for value in packs if value.pack_id == "gemma4-12b-q4-k-m-v1"
    )

    assert gemma12.installed
    assert gemma12.validated
    assert gemma12.model == "gemma4:12b"


def test_exact_canonical_tag_is_preferred_over_verified_alias(tmp_path):
    packs = manager(
        tmp_path,
        models=(
            local_gemma12(name="gemma4:12b"),
            local_gemma12(),
        ),
    ).catalog()

    gemma12 = next(
        value for value in packs if value.pack_id == "gemma4-12b-q4-k-m-v1"
    )

    assert gemma12.installed
    assert gemma12.model == "gemma4:12b-it-q4_K_M"


@pytest.mark.parametrize(
    "local",
    (
        local_gemma12(name="gemma4:12b", digest="0" * 64),
        local_gemma12(name="gemma4:12b", size_bytes=7_556_508_395),
        local_gemma12(name="gemma4:12b", quantization="Q5_K_M"),
        local_gemma12(name="gemma4:12b", family="gemma3"),
        local_gemma12(name="gemma4:../12b"),
        local_gemma12(name="gemma4:.."),
        local_gemma12(name=" gemma4:12b"),
        local_gemma12(name="other:12b"),
    ),
)
def test_alias_rejects_wrong_receipt_family_or_unsafe_tag(tmp_path, local):
    packs = manager(tmp_path, models=(local,)).catalog()
    gemma12 = next(
        value for value in packs if value.pack_id == "gemma4-12b-q4-k-m-v1"
    )

    assert not gemma12.installed
    assert not gemma12.validated
    assert gemma12.model == "gemma4:12b-it-q4_K_M"


def test_absent_signed_packs_are_never_marked_validated(tmp_path):
    packs = manager(tmp_path).catalog()

    assert all(not pack.installed for pack in packs)
    assert all(not pack.validated for pack in packs)


def test_ollama_pack_requires_exact_signed_size_and_quantization(tmp_path):
    wrong_size = manager(
        tmp_path / "size",
        models=(local_gemma12(size_bytes=7_556_508_395),),
    ).catalog()
    wrong_size = next(
        value for value in wrong_size if value.pack_id == "gemma4-12b-q4-k-m-v1"
    )
    normalized_but_not_exact = manager(
        tmp_path / "quant",
        models=(local_gemma12(quantization="q4-k-m"),),
    ).catalog()
    normalized_but_not_exact = next(
        value
        for value in normalized_but_not_exact
        if value.pack_id == "gemma4-12b-q4-k-m-v1"
    )

    assert not wrong_size.installed
    assert not wrong_size.validated
    assert not normalized_but_not_exact.installed
    assert not normalized_but_not_exact.validated


def test_get_refuses_locally_unvalidated_pack(tmp_path):
    value = manager(
        tmp_path,
        models=(local_gemma12(digest="0" * 64),),
    )

    with pytest.raises(ModelPackSecurityError, match="validated"):
        value.get("gemma4-12b-q4-k-m-v1")


def test_ensure_never_returns_pack_without_signed_validation_flag(
    tmp_path,
    monkeypatch,
):
    value = manager(tmp_path, models=(local_gemma12(),))
    manifest = copy.deepcopy(value._load_verified_manifest())
    for raw in manifest["packs"]:
        if raw["id"] == "gemma4-12b-q4-k-m-v1":
            raw["validated"] = False
    value._verified_manifest = manifest
    monkeypatch.setattr(value, "_pull_ollama", lambda _pack: None)

    with pytest.raises(ModelPackSecurityError, match="validated"):
        value.ensure(plan("gemma4-12b-q4-k-m-v1"), consent=True)


def test_tampered_manifest_fails_closed(tmp_path):
    source = resource_path("resources", "polish")
    target = tmp_path / "signed"
    shutil.copytree(source, target)
    manifest = target / "model-packs.json"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace('"11.95B"', '"99B"'),
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
        value.ensure(plan("gemma4-e4b-qat-v1"), consent=False)
    assert error.value.pack.size_bytes == 6_146_501_801
    assert "gemma" not in str(error.value).casefold()


def test_exact_installed_pack_needs_no_download_consent(tmp_path):
    value = manager(tmp_path, models=(local_gemma12(),))
    pack = value.ensure(plan("gemma4-12b-q4-k-m-v1"), consent=False)
    assert pack.installed


def test_pack_removal_is_explicit(tmp_path):
    writing = FakeWritingModels((local_gemma12(),))
    value = ModelPackManager(
        install_root=tmp_path / "packs",
        writing_models=writing,
    )
    value.remove("gemma4-12b-q4-k-m-v1")
    assert writing.removed == ["gemma4:12b-it-q4_K_M"]


def test_signed_manifest_pack_id_migration(tmp_path):
    value = manager(tmp_path)
    manifest = copy.deepcopy(value._load_verified_manifest())
    manifest["packs"][0]["replaces"] = ["qwen3-1.7b-old"]
    value._verified_manifest = manifest
    assert (
        value.migrate_pack_id("qwen3-1.7b-old")
        == "gemma4-e2b-qat-v1"
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
        family="Gemma 4",
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

    invalid_content = b"NOPE" + b"local model"
    value._verified_manifest["packs"][0]["manifest_digest"] = hashlib.sha256(
        invalid_content
    ).hexdigest()
    target.write_bytes(invalid_content)
    value._model_files.clear()
    with pytest.raises(ModelPackSecurityError, match="GGUF"):
        value.model_file(pack.pack_id)


def test_ollama_model_file_requires_exact_signed_blob_receipt(
    tmp_path,
    monkeypatch,
):
    content = b"GGUF" + b"exact model blob"
    blob = tmp_path / "ollama" / "blob.gguf"
    blob.parent.mkdir()
    blob.write_bytes(content)
    tag_digest = hashlib.sha256(b"signed tag manifest").hexdigest()
    raw = {
        "id": "test-ollama-pack",
        "display_name": "Test Ollama pack",
        "model": "test:exact",
        "family": "Gemma 4",
        "parameters": "test",
        "quantization": "Q4_0",
        "runtime": "ollama",
        "manifest_digest": tag_digest,
        "size_bytes": 123,
        "model_blob_digest": hashlib.sha256(content).hexdigest(),
        "model_blob_size": len(content),
        "min_ram_bytes": 1,
        "min_vram_bytes": 0,
        "max_context_tokens": 4096,
        "backends": ["cpu"],
        "license": "Apache-2.0",
        "source_url": "https://example.test",
        "download_ref": "ollama://test:exact",
        "validated": True,
    }
    writing = FakeWritingModels(
        (
            SimpleNamespace(
                name="test:exact",
                digest=tag_digest,
                size_bytes=123,
                quantization="Q4_0",
                family="gemma4",
            ),
        )
    )
    value = ModelPackManager(
        install_root=tmp_path / "packs",
        writing_models=writing,
    )
    value._verified_manifest = {
        "schema_version": 1,
        "policy_version": 3,
        "packs": [raw],
    }
    monkeypatch.setattr(
        "echo_link_core.polish_model_packs.shutil.which",
        lambda _name: "ollama.exe",
    )
    monkeypatch.setattr(
        "echo_link_core.polish_model_packs.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=f'FROM "{blob}"\n',
            stderr="",
        ),
    )

    assert value.model_file("test-ollama-pack") == blob.resolve()

    blob.write_bytes(b"GGUF" + b"wrong model blob")
    with pytest.raises(ModelPackSecurityError, match="digest"):
        value.model_file("test-ollama-pack")


def test_ollama_pack_without_signed_blob_receipt_fails_closed(tmp_path):
    value = manager(tmp_path)
    manifest = copy.deepcopy(value._load_verified_manifest())
    manifest["packs"][0].pop("model_blob_digest")
    value._verified_manifest = manifest

    with pytest.raises(ModelPackSecurityError, match="blob receipt"):
        value.catalog()
