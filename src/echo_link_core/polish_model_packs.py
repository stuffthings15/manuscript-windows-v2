from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Callable, Iterable

from .data_protection import validate_local_data_file, validate_local_directory
from .paths import data_root, resource_path
from .polish_types import ExecutionPlan, PolishModelPack, RuntimeBackend
from .writing_models import LocalWritingModelManager, WritingModelError


TRUSTED_KEY_ID = "manuscript-polish-2026-01"
TRUSTED_PUBLIC_KEY_FINGERPRINT = (
    "c0d994b686dc9401fefacb765e8d582cb6f170c6063440d233c93d15905e30fe"
)
_DIGEST_INFO_SHA256 = bytes.fromhex(
    "3031300d060960864801650304020105000420"
)
_MAX_MANIFEST_BYTES = 512 * 1024
_SAFE_OLLAMA_TAG = re.compile(
    r"[a-z0-9][a-z0-9._-]*"
    r"(?:/[a-z0-9][a-z0-9._-]*)*"
    r"(?::[a-z0-9][a-z0-9._-]*)?",
    re.IGNORECASE,
)


class ModelPackSecurityError(RuntimeError):
    pass


class ModelPackNotFoundError(RuntimeError):
    pass


class ModelPackConsentRequired(RuntimeError):
    def __init__(self, pack: PolishModelPack):
        self.pack = pack
        super().__init__(
            "A local AI pack needs explicit download approval "
            f"({pack.size_bytes / (1024**3):.1f} GB)."
        )


class ModelPackDownloadError(RuntimeError):
    pass


class ModelPackManager:
    """Verify the signed allowlist and install only exact approved model packs."""

    def __init__(
        self,
        *,
        manifest_path: str | Path | None = None,
        signature_path: str | Path | None = None,
        public_key_path: str | Path | None = None,
        install_root: str | Path | None = None,
        writing_models: LocalWritingModelManager | None = None,
    ) -> None:
        self.manifest_path = Path(
            manifest_path
            or resource_path("resources", "polish", "model-packs.json")
        )
        self.signature_path = Path(
            signature_path
            or resource_path("resources", "polish", "model-packs.sig")
        )
        self.public_key_path = Path(
            public_key_path
            or resource_path("resources", "polish", "model-packs-public-key.json")
        )
        self.install_root = Path(
            install_root or data_root() / "polish-model-packs"
        )
        self.writing_models = writing_models or LocalWritingModelManager()
        self._verified_manifest: dict | None = None
        self._model_files: dict[str, Path] = {}

    def catalog(self, *, refresh_installed: bool = True) -> tuple[PolishModelPack, ...]:
        manifest = self._load_verified_manifest()
        installed: tuple[object, ...] = ()
        if refresh_installed:
            try:
                values = self.writing_models.list_models(timeout=2.0)
            except (OSError, RuntimeError, ValueError, WritingModelError):
                values = ()
            installed = tuple(values)
        packs: list[PolishModelPack] = []
        for raw in manifest["packs"]:
            pack = _parse_pack(raw)
            if pack.runtime == "ollama":
                local = _verified_ollama_model(pack, installed)
                matches = local is not None
                pack = replace(
                    pack,
                    model=(
                        str(getattr(local, "name", pack.model)).strip()
                        if local is not None
                        else pack.model
                    ),
                    installed=matches,
                    validated=matches and bool(raw.get("validated")),
                )
            else:
                path = self.install_root / pack.pack_id / "model.gguf"
                if path.is_file():
                    matches = (
                        path.stat().st_size == pack.size_bytes
                        and hmac.compare_digest(_sha256(path), pack.manifest_digest)
                    )
                    pack = replace(
                        pack,
                        installed=matches,
                        validated=matches and bool(raw.get("validated")),
                    )
            packs.append(pack)
        return tuple(packs)

    def ensure(
        self,
        plan: ExecutionPlan,
        *,
        consent: bool,
        progress: Callable[[int, int], None] | None = None,
    ) -> PolishModelPack:
        if not plan.use_llm or not plan.pack_id:
            raise ModelPackNotFoundError("The execution plan does not require a model pack.")
        pack = self._find_pack(plan.pack_id)
        if pack.installed and pack.validated:
            return pack
        if pack.installed:
            raise ModelPackSecurityError(
                "The installed model pack is not validated by the signed allowlist."
            )
        if not consent:
            raise ModelPackConsentRequired(pack)
        if not pack.available:
            raise ModelPackDownloadError("This model pack is not available for download.")
        if pack.runtime == "ollama":
            self._pull_ollama(pack)
        else:
            self._download_file(pack, progress=progress)
        installed = self._find_pack(pack.pack_id)
        if not installed.installed or not installed.validated:
            raise ModelPackSecurityError(
                "The downloaded model was not validated by the signed allowlist."
            )
        return installed

    def get(self, pack_id: str) -> PolishModelPack:
        pack = self._find_pack(pack_id)
        if not pack.validated:
            raise ModelPackSecurityError(
                "The installed model pack is not validated by the signed allowlist."
            )
        return pack

    def _find_pack(self, pack_id: str) -> PolishModelPack:
        wanted = str(pack_id).strip()
        for pack in self.catalog(refresh_installed=True):
            if pack.pack_id == wanted:
                return pack
        raise ModelPackNotFoundError(f"Unknown Polish model pack: {wanted}")

    def migrate_pack_id(self, pack_id: str) -> str:
        manifest = self._load_verified_manifest()
        wanted = str(pack_id).strip()
        replacements = {
            str(old): str(raw["id"])
            for raw in manifest["packs"]
            for old in raw.get("replaces", ())
        }
        return replacements.get(wanted, wanted)

    def remove(self, pack_id: str) -> None:
        pack = self._find_pack(pack_id)
        if pack.runtime == "ollama":
            self.writing_models.remove_model(pack.model)
            return
        target = (self.install_root / pack.pack_id).resolve()
        root = self.install_root.resolve()
        if target.parent != root:
            raise ModelPackSecurityError("Model pack removal target is unsafe.")
        if target.exists():
            shutil.rmtree(target)

    def model_file(self, pack_id: str) -> Path:
        """Return an exact local GGUF path without copying model weights."""
        pack = self.get(pack_id)
        expected_digest, expected_size = self._model_blob_receipt(pack)
        cached = self._model_files.get(pack.pack_id)
        if cached is not None and cached.is_file():
            _verify_exact_gguf(cached, expected_digest, expected_size)
            return cached
        if pack.runtime != "ollama":
            path = validate_local_data_file(
                self.install_root / pack.pack_id / "model.gguf",
                purpose="Polish model",
            )
            if not path.is_file():
                raise ModelPackNotFoundError("Installed Polish GGUF was not found.")
            _verify_exact_gguf(path, expected_digest, expected_size)
            self._model_files[pack.pack_id] = path
            return path

        executable = self.writing_models.ollama_executable
        executable = executable or shutil.which("ollama") or shutil.which("ollama.exe")
        if not executable:
            raise ModelPackNotFoundError("Local AI storage is unavailable.")
        try:
            completed = subprocess.run(
                [str(executable), "show", pack.model, "--modelfile"],
                capture_output=True,
                text=True,
                timeout=10.0,
                check=False,
                creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ModelPackNotFoundError(
                f"Could not locate the local GGUF: {exc}"
            ) from exc
        if completed.returncode != 0:
            raise ModelPackNotFoundError("Local AI did not expose its model file.")
        source = ""
        for line in completed.stdout.splitlines():
            if line.strip().upper().startswith("FROM "):
                source = line.strip()[5:].strip().strip('"')
                break
        if not source or not Path(source).is_absolute():
            raise ModelPackSecurityError("Local AI returned an unsafe model path.")
        try:
            path = validate_local_data_file(source, purpose="Polish model")
        except (OSError, RuntimeError, ValueError) as exc:
            raise ModelPackSecurityError("Local AI model path failed validation.") from exc
        if not path.is_file():
            raise ModelPackNotFoundError("The local AI model file was not found.")
        _verify_exact_gguf(path, expected_digest, expected_size)
        self._model_files[pack.pack_id] = path
        return path

    def _model_blob_receipt(self, pack: PolishModelPack) -> tuple[str, int]:
        if pack.runtime != "ollama":
            return pack.manifest_digest, pack.size_bytes
        manifest = self._load_verified_manifest()
        raw = next(
            (
                value
                for value in manifest["packs"]
                if isinstance(value, dict) and value.get("id") == pack.pack_id
            ),
            None,
        )
        if raw is None:
            raise ModelPackSecurityError("Signed model blob receipt is missing.")
        digest = str(raw.get("model_blob_digest") or "").casefold()
        try:
            size = int(raw.get("model_blob_size"))
        except (TypeError, ValueError) as exc:
            raise ModelPackSecurityError("Signed model blob receipt is invalid.") from exc
        if (
            len(digest) != 64
            or any(value not in "0123456789abcdef" for value in digest)
            or not 1 <= size <= 64 * 1024**3
        ):
            raise ModelPackSecurityError("Signed model blob receipt is invalid.")
        return digest, size

    def _pull_ollama(self, pack: PolishModelPack) -> None:
        free = shutil.disk_usage(Path.home()).free
        if free < pack.size_bytes + 1024**3:
            raise ModelPackDownloadError(
                "Not enough disk space for the selected local model pack."
            )
        executable = self.writing_models.ollama_executable
        executable = executable or shutil.which("ollama") or shutil.which("ollama.exe")
        if not executable:
            raise ModelPackDownloadError("The local AI service is not installed.")
        try:
            completed = subprocess.run(
                [str(executable), "pull", pack.model],
                capture_output=True,
                text=True,
                timeout=60 * 60,
                check=False,
                creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ModelPackDownloadError(f"Local model download failed: {exc}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "Local AI failed.").strip()
            lowered = detail.casefold()
            if any(
                marker in lowered
                for marker in ("connectex", "dial tcp", "connection", "timeout")
            ):
                raise ModelPackDownloadError(
                    "Local AI download could not reach the model service. "
                    "Check the internet connection and firewall, then retry."
                )
            raise ModelPackDownloadError("Local AI download failed. Retry from Settings.")

    def _download_file(
        self,
        pack: PolishModelPack,
        *,
        progress: Callable[[int, int], None] | None,
    ) -> None:
        parsed = urllib.parse.urlsplit(pack.download_ref)
        if parsed.scheme.casefold() != "https" or not parsed.hostname:
            raise ModelPackSecurityError("Model download URL must use HTTPS.")
        root = validate_local_directory(
            self.install_root,
            purpose="Polish model-pack folder",
            create=True,
        )
        target_dir = validate_local_directory(
            root / pack.pack_id,
            purpose="Polish model pack",
            create=True,
        )
        if target_dir.parent != root:
            raise ModelPackSecurityError("Model pack download target is unsafe.")
        if shutil.disk_usage(target_dir).free < pack.size_bytes + 512 * 1024**2:
            raise ModelPackDownloadError(
                "Not enough disk space for the selected local model pack."
            )
        target = target_dir / "model.gguf"
        partial = target_dir / "model.gguf.partial"
        if partial.exists():
            partial.unlink()
        request = urllib.request.Request(
            pack.download_ref,
            headers={"User-Agent": "Manuscript-Polish/1"},
            method="GET",
        )
        opener = urllib.request.build_opener(_HTTPSRedirectHandler())
        downloaded = 0
        digest = hashlib.sha256()
        try:
            with opener.open(request, timeout=30.0) as response:
                final_url = urllib.parse.urlsplit(response.geturl())
                if final_url.scheme.casefold() != "https":
                    raise ModelPackSecurityError("Model download left HTTPS.")
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) != pack.size_bytes:
                    raise ModelPackSecurityError(
                        "Model download size does not match the signed manifest."
                    )
                with partial.open("xb") as handle:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        downloaded += len(chunk)
                        if downloaded > pack.size_bytes:
                            raise ModelPackSecurityError(
                                "Model download exceeded the signed size."
                            )
                        handle.write(chunk)
                        digest.update(chunk)
                        if progress is not None:
                            progress(downloaded, pack.size_bytes)
                    handle.flush()
                    os.fsync(handle.fileno())
            if downloaded != pack.size_bytes:
                raise ModelPackSecurityError(
                    "Model download was interrupted or incomplete."
                )
            if not hmac.compare_digest(digest.hexdigest(), pack.manifest_digest):
                raise ModelPackSecurityError("Model SHA-256 verification failed.")
            os.replace(partial, target)
        except (
            ModelPackSecurityError,
            OSError,
            TypeError,
            ValueError,
            urllib.error.URLError,
        ) as exc:
            if partial.exists():
                partial.unlink()
            if isinstance(exc, ModelPackSecurityError):
                raise
            raise ModelPackDownloadError(f"Model download failed: {exc}") from exc

    def _load_verified_manifest(self) -> dict:
        if self._verified_manifest is not None:
            return self._verified_manifest
        try:
            data = self.manifest_path.read_bytes()
            signature = base64.b64decode(
                self.signature_path.read_text(encoding="ascii").strip(),
                validate=True,
            )
            public = json.loads(self.public_key_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ModelPackSecurityError(
                "Signed Polish model manifest is missing or invalid."
            ) from exc
        if not data or len(data) > _MAX_MANIFEST_BYTES:
            raise ModelPackSecurityError("Polish model manifest size is invalid.")
        if public.get("key_id") != TRUSTED_KEY_ID:
            raise ModelPackSecurityError("Polish model manifest key is not trusted.")
        fingerprint = _public_key_fingerprint(public)
        if not hmac.compare_digest(fingerprint, TRUSTED_PUBLIC_KEY_FINGERPRINT):
            raise ModelPackSecurityError("Polish model manifest public key changed.")
        if not _verify_rsa_sha256(data, signature, public):
            raise ModelPackSecurityError("Polish model manifest signature failed.")
        try:
            manifest = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelPackSecurityError("Polish model manifest JSON is invalid.") from exc
        _validate_manifest(manifest)
        self._verified_manifest = manifest
        return manifest


class _HTTPSRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        parsed = urllib.parse.urlsplit(new_url)
        if parsed.scheme.casefold() != "https":
            raise ModelPackSecurityError("Model download redirect left HTTPS.")
        return super().redirect_request(request, fp, code, message, headers, new_url)


def _verify_rsa_sha256(data: bytes, signature: bytes, public: dict) -> bool:
    try:
        modulus = int.from_bytes(
            base64.b64decode(public["modulus_base64"], validate=True),
            "big",
        )
        exponent = int(public["exponent"])
    except (KeyError, TypeError, ValueError):
        return False
    if modulus.bit_length() < 3072 or exponent != 65537:
        return False
    length = (modulus.bit_length() + 7) // 8
    if len(signature) != length:
        return False
    encoded = pow(int.from_bytes(signature, "big"), exponent, modulus).to_bytes(length, "big")
    digest = hashlib.sha256(data).digest()
    padding_size = length - len(_DIGEST_INFO_SHA256) - len(digest) - 3
    if padding_size < 8:
        return False
    expected = (
        b"\x00\x01"
        + b"\xff" * padding_size
        + b"\x00"
        + _DIGEST_INFO_SHA256
        + digest
    )
    return hmac.compare_digest(encoded, expected)


def _public_key_fingerprint(public: dict) -> str:
    material = {
        "key_id": str(public.get("key_id") or ""),
        "modulus_base64": str(public.get("modulus_base64") or ""),
        "exponent": int(public.get("exponent") or 0),
    }
    raw = json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _validate_manifest(manifest: object) -> None:
    if not isinstance(manifest, dict):
        raise ModelPackSecurityError("Polish model manifest must be an object.")
    if manifest.get("schema_version") != 1:
        raise ModelPackSecurityError("Unsupported Polish model manifest schema.")
    if manifest.get("policy_version") not in {1, 2, 3}:
        raise ModelPackSecurityError("Unsupported Polish model policy.")
    packs = manifest.get("packs")
    if not isinstance(packs, list) or not packs:
        raise ModelPackSecurityError("Polish model manifest has no packs.")
    seen: set[str] = set()
    for raw in packs:
        pack = _parse_pack(raw)
        if pack.pack_id in seen:
            raise ModelPackSecurityError("Polish model pack IDs must be unique.")
        seen.add(pack.pack_id)


def _parse_pack(raw: object) -> PolishModelPack:
    if not isinstance(raw, dict):
        raise ModelPackSecurityError("Polish model pack entry is invalid.")
    try:
        pack_id = str(raw["id"])
        digest = str(raw["manifest_digest"]).casefold()
        size_bytes = int(raw["size_bytes"])
        min_ram = int(raw["min_ram_bytes"])
        min_vram = int(raw["min_vram_bytes"])
        max_context = int(raw["max_context_tokens"])
        backends = tuple(RuntimeBackend(str(value)) for value in raw["backends"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ModelPackSecurityError("Polish model pack fields are invalid.") from exc
    if not pack_id or len(pack_id) > 100:
        raise ModelPackSecurityError("Polish model pack ID is invalid.")
    if not all(character.isalnum() or character in "._-" for character in pack_id):
        raise ModelPackSecurityError("Polish model pack ID is unsafe.")
    if len(digest) != 64 or any(value not in "0123456789abcdef" for value in digest):
        raise ModelPackSecurityError("Polish model pack digest is invalid.")
    if not 1 <= size_bytes <= 64 * 1024**3:
        raise ModelPackSecurityError("Polish model pack size is invalid.")
    if min_ram < 0 or min_vram < 0 or not 512 <= max_context <= 131_072:
        raise ModelPackSecurityError("Polish model pack limits are invalid.")
    if not backends:
        raise ModelPackSecurityError("Polish model pack has no runtime backend.")
    if raw.get("validated") is not True:
        raise ModelPackSecurityError(
            "Polish model pack is not marked validated by the signed manifest."
        )
    model = str(raw.get("model") or "")
    runtime = str(raw.get("runtime") or "")
    if runtime == "ollama" and not _is_safe_ollama_tag(model):
        raise ModelPackSecurityError("Signed Ollama model tag is unsafe.")
    if runtime == "ollama":
        blob_digest = str(raw.get("model_blob_digest") or "").casefold()
        try:
            blob_size = int(raw.get("model_blob_size"))
        except (TypeError, ValueError) as exc:
            raise ModelPackSecurityError(
                "Signed Ollama model blob receipt is invalid."
            ) from exc
        if (
            len(blob_digest) != 64
            or any(value not in "0123456789abcdef" for value in blob_digest)
            or not 1 <= blob_size <= 64 * 1024**3
        ):
            raise ModelPackSecurityError(
                "Signed Ollama model blob receipt is invalid."
            )
    return PolishModelPack(
        pack_id=pack_id,
        display_name=str(raw["display_name"])[:120],
        model=model,
        family=str(raw["family"])[:80],
        parameters=str(raw["parameters"])[:40],
        quantization=str(raw["quantization"])[:40],
        runtime=runtime[:40],
        manifest_digest=digest,
        size_bytes=size_bytes,
        min_ram_bytes=min_ram,
        min_vram_bytes=min_vram,
        max_context_tokens=max_context,
        supported_backends=backends,
        license_id=str(raw["license"])[:80],
        source_url=str(raw["source_url"])[:500],
        download_ref=str(raw["download_ref"])[:500],
        version=str(raw.get("version") or "1")[:40],
        available=bool(raw.get("available", True)),
        installed=False,
        validated=False,
    )


def _is_safe_ollama_tag(value: object) -> bool:
    tag = str(value or "")
    return bool(
        tag
        and tag == tag.strip()
        and len(tag) <= 120
        and ".." not in tag
        and _SAFE_OLLAMA_TAG.fullmatch(tag)
    )


def _same_ollama_repository(candidate: str, canonical: str) -> bool:
    candidate_repository, candidate_separator, _candidate_tag = candidate.partition(":")
    canonical_repository, canonical_separator, _canonical_tag = canonical.partition(":")
    return bool(
        candidate_separator
        and canonical_separator
        and hmac.compare_digest(
            candidate_repository.casefold(),
            canonical_repository.casefold(),
        )
    )


def _same_model_family(left: str, right: str) -> bool:
    normalize = lambda value: re.sub(r"[^a-z0-9]+", "", str(value).casefold())
    normalized_left = normalize(left)
    normalized_right = normalize(right)
    return bool(
        normalized_left
        and normalized_right
        and hmac.compare_digest(normalized_left, normalized_right)
    )


def _ollama_receipt_matches(local: object, pack: PolishModelPack) -> bool:
    try:
        size = int(getattr(local, "size_bytes"))
    except (AttributeError, TypeError, ValueError):
        return False
    digest = str(getattr(local, "digest", "") or "").casefold()
    quantization = str(getattr(local, "quantization", "") or "")
    family = str(getattr(local, "family", "") or "")
    return bool(
        hmac.compare_digest(digest, pack.manifest_digest.casefold())
        and size == pack.size_bytes
        and _same_quantization(quantization, pack.quantization)
        and _same_model_family(family, pack.family)
    )


def _verified_ollama_model(
    pack: PolishModelPack,
    installed: Iterable[object],
) -> object | None:
    """Select the canonical tag, or an exact-receipt alias in its repository."""
    exact: list[object] = []
    aliases: list[object] = []
    canonical = pack.model
    for local in installed:
        name = str(getattr(local, "name", "") or "")
        if (
            not _is_safe_ollama_tag(name)
            or not _same_ollama_repository(name, canonical)
            or not _ollama_receipt_matches(local, pack)
        ):
            continue
        if hmac.compare_digest(name.casefold(), canonical.casefold()):
            exact.append(local)
        else:
            aliases.append(local)
    candidates = exact or aliases
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda value: (
            len(str(getattr(value, "name", ""))),
            str(getattr(value, "name", "")).casefold(),
        ),
    )


def _same_quantization(left: str, right: str) -> bool:
    return hmac.compare_digest(str(left).strip(), str(right).strip())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_gguf(path: Path) -> None:
    try:
        with path.open("rb") as handle:
            magic = handle.read(4)
    except OSError as exc:
        raise ModelPackNotFoundError(f"Polish model could not be read: {exc}") from exc
    if magic != b"GGUF":
        raise ModelPackSecurityError("Polish model is not a GGUF file.")


def _verify_exact_gguf(path: Path, digest: str, size: int) -> None:
    _require_gguf(path)
    try:
        actual_size = path.stat().st_size
    except OSError as exc:
        raise ModelPackNotFoundError(f"Polish model could not be read: {exc}") from exc
    if actual_size != int(size):
        raise ModelPackSecurityError(
            "Polish model blob size does not match the signed receipt."
        )
    if not hmac.compare_digest(_sha256(path), str(digest).casefold()):
        raise ModelPackSecurityError(
            "Polish model blob digest does not match the signed receipt."
        )


__all__ = [
    "ModelPackConsentRequired",
    "ModelPackDownloadError",
    "ModelPackManager",
    "ModelPackNotFoundError",
    "ModelPackSecurityError",
    "TRUSTED_KEY_ID",
]
