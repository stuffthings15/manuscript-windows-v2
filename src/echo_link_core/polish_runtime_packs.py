from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Callable

from .data_protection import validate_local_data_file, validate_local_directory
from .paths import data_root, resource_path
from .polish_model_packs import (
    TRUSTED_KEY_ID,
    TRUSTED_PUBLIC_KEY_FINGERPRINT,
    _public_key_fingerprint,
    _verify_rsa_sha256,
)
from .polish_types import RuntimeBackend


_MAX_MANIFEST_BYTES = 512 * 1024
_MAX_ARCHIVE_BYTES = 2 * 1024**3
_MAX_EXTRACTED_BYTES = 4 * 1024**3
_MAX_EXTRACTED_FILES = 4096
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
_SAFE_FILE = re.compile(r"^[A-Za-z0-9._+ -]{1,180}$")


class RuntimePackError(RuntimeError):
    pass


class RuntimePackSecurityError(RuntimePackError):
    pass


class RuntimePackDownloadError(RuntimePackError):
    pass


class RuntimePackNotFoundError(RuntimePackError):
    pass


class RuntimePackConsentRequired(RuntimePackError):
    def __init__(self, pack: "PolishRuntimePack") -> None:
        self.pack = pack
        size_mib = pack.download_size_bytes / (1024**2)
        super().__init__(
            "Local AI support files need explicit download approval "
            f"({size_mib:.0f} MB)."
        )


@dataclass(frozen=True, slots=True)
class RuntimePackComponent:
    filename: str
    url: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class PolishRuntimePack:
    pack_id: str
    display_name: str
    backend: RuntimeBackend
    version: str
    architecture: str
    entry_point: str
    components: tuple[RuntimePackComponent, ...]
    license_id: str
    source_url: str
    available: bool = True
    installed: bool = False
    install_path: str = ""
    probe_detail: str = ""

    @property
    def download_size_bytes(self) -> int:
        return sum(value.size_bytes for value in self.components)

    @property
    def executable_path(self) -> Path:
        if not self.install_path:
            return Path()
        return Path(self.install_path) / self.entry_point


class RuntimePackManager:
    """Install only signed, exact llama.cpp backend archives."""

    def __init__(
        self,
        *,
        manifest_path: str | Path | None = None,
        signature_path: str | Path | None = None,
        public_key_path: str | Path | None = None,
        install_root: str | Path | None = None,
        runner: Callable[..., subprocess.CompletedProcess] | None = None,
    ) -> None:
        self.manifest_path = Path(
            manifest_path
            or resource_path("resources", "polish", "runtime-packs.json")
        )
        self.signature_path = Path(
            signature_path
            or resource_path("resources", "polish", "runtime-packs.sig")
        )
        self.public_key_path = Path(
            public_key_path
            or resource_path("resources", "polish", "model-packs-public-key.json")
        )
        self.install_root = Path(
            install_root or data_root() / "polish-runtime-packs"
        )
        self.runner = runner or subprocess.run
        self._verified_manifest: dict | None = None

    def catalog(self, *, refresh_installed: bool = True) -> tuple[PolishRuntimePack, ...]:
        manifest = self._load_verified_manifest()
        values: list[PolishRuntimePack] = []
        for raw in manifest["packs"]:
            pack = _parse_pack(raw)
            if refresh_installed:
                pack = self._installed_state(pack)
            values.append(pack)
        return tuple(values)

    def candidates(
        self,
        backend: RuntimeBackend,
        *,
        refresh_installed: bool = True,
    ) -> tuple[PolishRuntimePack, ...]:
        wanted = RuntimeBackend(backend)
        values = [
            pack
            for pack in self.catalog(refresh_installed=refresh_installed)
            if pack.backend is wanted
            and pack.available
            and pack.architecture == _architecture()
        ]
        if wanted is RuntimeBackend.CUDA:
            cuda_major = _cuda_umd_major()
            values.sort(
                key=lambda pack: (
                    0
                    if (cuda_major >= 13 and "cuda-13" in pack.pack_id)
                    or (cuda_major < 13 and "cuda-12" in pack.pack_id)
                    else 1,
                    pack.pack_id,
                )
            )
        else:
            values.sort(key=lambda pack: pack.pack_id)
        return tuple(values)

    def select(self, backend: RuntimeBackend) -> PolishRuntimePack:
        candidates = self.candidates(backend)
        if not candidates:
            raise RuntimePackNotFoundError(
                f"No signed {RuntimeBackend(backend).value.upper()} runtime pack fits."
            )
        installed = next((value for value in candidates if value.installed), None)
        return installed or candidates[0]

    def ensure(
        self,
        backend: RuntimeBackend,
        *,
        consent: bool,
        progress: Callable[[str, int, int], None] | None = None,
    ) -> PolishRuntimePack:
        pack = self.select(backend)
        if pack.installed:
            return pack
        if not consent:
            raise RuntimePackConsentRequired(pack)
        self._install(pack, progress=progress)
        installed = next(
            value for value in self.catalog() if value.pack_id == pack.pack_id
        )
        if not installed.installed:
            raise RuntimePackSecurityError(
                "Installed llama.cpp runtime failed its integrity receipt."
            )
        return installed

    def probe(
        self,
        pack: PolishRuntimePack,
        *,
        timeout: float = 30.0,
    ) -> PolishRuntimePack:
        if not pack.installed or not pack.executable_path.is_file():
            raise RuntimePackNotFoundError("llama.cpp runtime is not installed.")
        try:
            completed = self.runner(
                [str(pack.executable_path), "--list-devices"],
                cwd=str(pack.executable_path.parent),
                capture_output=True,
                text=True,
                timeout=max(1.0, float(timeout)),
                check=False,
                creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimePackError(f"llama.cpp backend probe failed: {exc}") from exc
        output = "\n".join(
            value.strip()
            for value in (completed.stdout, completed.stderr)
            if value and value.strip()
        )
        if completed.returncode != 0:
            raise RuntimePackError(
                f"llama.cpp backend probe failed: {output[-500:] or 'no detail'}"
            )
        folded = output.casefold()
        if pack.backend is RuntimeBackend.CPU:
            accepted = bool(output)
        else:
            markers = {
                RuntimeBackend.CUDA: ("cuda", "nvidia"),
                RuntimeBackend.HIP: ("hip", "rocm", "amd"),
                RuntimeBackend.VULKAN: ("vulkan",),
                RuntimeBackend.SYCL: ("sycl", "oneapi", "intel"),
            }[pack.backend]
            accepted = any(value in folded for value in markers)
        if not accepted:
            raise RuntimePackError(
                f"{pack.backend.value.upper()} runtime found no matching compute device."
            )
        return replace(pack, probe_detail=output[-1000:])

    def remove(self, pack_id: str) -> None:
        wanted = str(pack_id).strip()
        if not _SAFE_ID.fullmatch(wanted):
            raise RuntimePackSecurityError("Runtime pack removal target is unsafe.")
        root = self.install_root.resolve()
        target = (root / wanted).resolve()
        if target.parent != root:
            raise RuntimePackSecurityError("Runtime pack removal target is unsafe.")
        if target.exists():
            shutil.rmtree(target)

    def _installed_state(self, pack: PolishRuntimePack) -> PolishRuntimePack:
        try:
            root = validate_local_directory(
                self.install_root,
                purpose="Polish runtime-pack folder",
                create=False,
            )
            target = validate_local_directory(
                root / pack.pack_id,
                purpose="Installed Polish runtime",
                create=False,
            )
            if target.parent != root:
                return pack
        except (OSError, RuntimeError, ValueError):
            return pack
        receipt_path = target / "install-receipt.json"
        executable = target / pack.entry_point
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            files = receipt["files"]
            if (
                receipt.get("pack_id") != pack.pack_id
                or receipt.get("version") != pack.version
                or not isinstance(files, list)
                or not executable.is_file()
                or len(files) > _MAX_EXTRACTED_FILES
            ):
                return pack
            for raw in files:
                relative = _safe_relative_path(str(raw["path"]))
                path = validate_local_data_file(
                    target / relative,
                    purpose="Installed Polish runtime file",
                )
                if (
                    path.parent != (target / relative).parent.resolve()
                    or target not in path.parents
                    or not path.is_file()
                    or path.stat().st_size != int(raw["size"])
                    or not hmac.compare_digest(
                        _sha256(path),
                        str(raw["sha256"]).casefold(),
                    )
                ):
                    return pack
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            return pack
        return replace(pack, installed=True, install_path=str(target))

    def _install(
        self,
        pack: PolishRuntimePack,
        *,
        progress: Callable[[str, int, int], None] | None,
    ) -> None:
        root = validate_local_directory(
            self.install_root,
            purpose="Polish runtime-pack folder",
            create=True,
        )
        required = pack.download_size_bytes + 1024**3
        if shutil.disk_usage(root).free < required:
            raise RuntimePackDownloadError(
                "Not enough disk space for the selected llama.cpp runtime."
            )
        staging = Path(
            tempfile.mkdtemp(prefix=f".{pack.pack_id}-", dir=str(root))
        )
        payload = staging / "payload"
        payload.mkdir()
        try:
            for component in pack.components:
                archive = staging / component.filename
                self._download_component(
                    component,
                    archive,
                    progress=progress,
                )
                _safe_extract_zip(archive, payload)
                archive.unlink()
            executable = payload / pack.entry_point
            if not executable.is_file():
                raise RuntimePackSecurityError(
                    "Signed runtime archive has no expected entry point."
                )
            files = _file_receipt(payload)
            receipt = {
                "schema_version": 1,
                "pack_id": pack.pack_id,
                "version": pack.version,
                "backend": pack.backend.value,
                "files": files,
            }
            (payload / "install-receipt.json").write_text(
                json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            target = root / pack.pack_id
            if target.exists():
                verified_target = validate_local_directory(
                    target,
                    purpose="Existing Polish runtime",
                    create=False,
                )
                if verified_target.parent != root:
                    raise RuntimePackSecurityError(
                        "Existing runtime replacement target is unsafe."
                    )
                shutil.rmtree(verified_target)
            os.replace(payload, target)
        except (
            OSError,
            RuntimePackError,
            TypeError,
            ValueError,
            zipfile.BadZipFile,
        ):
            raise
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    def _download_component(
        self,
        component: RuntimePackComponent,
        target: Path,
        *,
        progress: Callable[[str, int, int], None] | None,
    ) -> None:
        parsed = urllib.parse.urlsplit(component.url)
        if parsed.scheme.casefold() != "https" or not parsed.hostname:
            raise RuntimePackSecurityError("Runtime download URL must use HTTPS.")
        request = urllib.request.Request(
            component.url,
            headers={"User-Agent": "Manuscript-Polish/2"},
            method="GET",
        )
        opener = urllib.request.build_opener(_HTTPSRedirectHandler())
        downloaded = 0
        digest = hashlib.sha256()
        try:
            with opener.open(request, timeout=30.0) as response:
                final = urllib.parse.urlsplit(response.geturl())
                if final.scheme.casefold() != "https":
                    raise RuntimePackSecurityError("Runtime download left HTTPS.")
                declared = response.headers.get("Content-Length")
                if declared and int(declared) != component.size_bytes:
                    raise RuntimePackSecurityError(
                        "Runtime download size does not match the signed manifest."
                    )
                with target.open("xb") as handle:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        downloaded += len(chunk)
                        if downloaded > component.size_bytes:
                            raise RuntimePackSecurityError(
                                "Runtime download exceeded the signed size."
                            )
                        handle.write(chunk)
                        digest.update(chunk)
                        if progress is not None:
                            progress(
                                component.filename,
                                downloaded,
                                component.size_bytes,
                            )
                    handle.flush()
                    os.fsync(handle.fileno())
            if downloaded != component.size_bytes:
                raise RuntimePackSecurityError(
                    "Runtime download was interrupted or incomplete."
                )
            if not hmac.compare_digest(digest.hexdigest(), component.sha256):
                raise RuntimePackSecurityError(
                    "Runtime archive SHA-256 verification failed."
                )
        except (
            OSError,
            TypeError,
            ValueError,
            urllib.error.URLError,
        ) as exc:
            if target.exists():
                target.unlink()
            if isinstance(exc, RuntimePackError):
                raise
            raise RuntimePackDownloadError(f"Runtime download failed: {exc}") from exc

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
            raise RuntimePackSecurityError(
                "Signed Polish runtime manifest is missing or invalid."
            ) from exc
        if not data or len(data) > _MAX_MANIFEST_BYTES:
            raise RuntimePackSecurityError("Polish runtime manifest size is invalid.")
        if public.get("key_id") != TRUSTED_KEY_ID:
            raise RuntimePackSecurityError("Polish runtime manifest key is not trusted.")
        if not hmac.compare_digest(
            _public_key_fingerprint(public),
            TRUSTED_PUBLIC_KEY_FINGERPRINT,
        ):
            raise RuntimePackSecurityError("Polish runtime public key changed.")
        if not _verify_rsa_sha256(data, signature, public):
            raise RuntimePackSecurityError("Polish runtime manifest signature failed.")
        try:
            manifest = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimePackSecurityError("Polish runtime JSON is invalid.") from exc
        _validate_manifest(manifest)
        self._verified_manifest = manifest
        return manifest


class _HTTPSRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        if urllib.parse.urlsplit(new_url).scheme.casefold() != "https":
            raise RuntimePackSecurityError("Runtime redirect left HTTPS.")
        return super().redirect_request(request, fp, code, message, headers, new_url)


def _validate_manifest(manifest: object) -> None:
    if not isinstance(manifest, dict):
        raise RuntimePackSecurityError("Polish runtime manifest must be an object.")
    if manifest.get("schema_version") != 1:
        raise RuntimePackSecurityError("Unsupported Polish runtime schema.")
    if manifest.get("key_id") != TRUSTED_KEY_ID:
        raise RuntimePackSecurityError("Polish runtime key ID changed.")
    packs = manifest.get("packs")
    if not isinstance(packs, list) or not packs:
        raise RuntimePackSecurityError("Polish runtime manifest has no packs.")
    seen: set[str] = set()
    for raw in packs:
        pack = _parse_pack(raw)
        if pack.pack_id in seen:
            raise RuntimePackSecurityError("Polish runtime pack IDs must be unique.")
        seen.add(pack.pack_id)


def _parse_pack(raw: object) -> PolishRuntimePack:
    if not isinstance(raw, dict):
        raise RuntimePackSecurityError("Polish runtime pack entry is invalid.")
    try:
        pack_id = str(raw["id"])
        backend = RuntimeBackend(str(raw["backend"]))
        architecture = str(raw["architecture"])
        entry_point = str(raw["entry_point"])
        components = tuple(_parse_component(value) for value in raw["components"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimePackSecurityError("Polish runtime pack fields are invalid.") from exc
    if not _SAFE_ID.fullmatch(pack_id):
        raise RuntimePackSecurityError("Polish runtime pack ID is unsafe.")
    if backend is RuntimeBackend.RULES:
        raise RuntimePackSecurityError("Rules are not a runtime package.")
    if architecture not in {"x64", "arm64"}:
        raise RuntimePackSecurityError("Runtime architecture is invalid.")
    relative = _safe_relative_path(entry_point)
    if relative.as_posix() != entry_point.replace("\\", "/"):
        raise RuntimePackSecurityError("Runtime entry point is unsafe.")
    if not components:
        raise RuntimePackSecurityError("Runtime pack has no components.")
    return PolishRuntimePack(
        pack_id=pack_id,
        display_name=str(raw["display_name"])[:120],
        backend=backend,
        version=str(raw["version"])[:40],
        architecture=architecture,
        entry_point=relative.as_posix(),
        components=components,
        license_id=str(raw["license"])[:80],
        source_url=str(raw["source_url"])[:500],
        available=bool(raw.get("available", True)),
    )


def _parse_component(raw: object) -> RuntimePackComponent:
    if not isinstance(raw, dict):
        raise RuntimePackSecurityError("Runtime component is invalid.")
    try:
        filename = str(raw["filename"])
        url = str(raw["url"])
        size_bytes = int(raw["size_bytes"])
        digest = str(raw["sha256"]).casefold()
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimePackSecurityError("Runtime component fields are invalid.") from exc
    if not _SAFE_FILE.fullmatch(filename) or "/" in filename or "\\" in filename:
        raise RuntimePackSecurityError("Runtime component filename is unsafe.")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        raise RuntimePackSecurityError("Runtime component URL must use HTTPS.")
    if not 1 <= size_bytes <= _MAX_ARCHIVE_BYTES:
        raise RuntimePackSecurityError("Runtime component size is invalid.")
    if len(digest) != 64 or any(value not in "0123456789abcdef" for value in digest):
        raise RuntimePackSecurityError("Runtime component digest is invalid.")
    return RuntimePackComponent(filename, url, size_bytes, digest)


def _safe_extract_zip(archive: Path, target: Path) -> None:
    total = 0
    count = 0
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            if info.is_dir():
                continue
            count += 1
            total += max(0, int(info.file_size))
            if count > _MAX_EXTRACTED_FILES or total > _MAX_EXTRACTED_BYTES:
                raise RuntimePackSecurityError("Runtime archive expands beyond its limit.")
            # Reject Unix symlinks and every absolute/traversal path.
            mode = (info.external_attr >> 16) & 0xFFFF
            if mode and (mode & 0o170000) == 0o120000:
                raise RuntimePackSecurityError("Runtime archive contains a link.")
            relative = _safe_relative_path(info.filename)
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source:
                if destination.exists():
                    incoming = source.read()
                    if destination.read_bytes() != incoming:
                        raise RuntimePackSecurityError(
                            "Runtime components contain conflicting files."
                        )
                else:
                    with destination.open("xb") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)


def _safe_relative_path(value: str) -> PurePosixPath:
    normalized = str(value).replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or ":" in path.parts[0]
    ):
        raise RuntimePackSecurityError("Runtime archive path is unsafe.")
    return path


def _file_receipt(root: Path) -> list[dict[str, str | int]]:
    values: list[dict[str, str | int]] = []
    total = 0
    for path in sorted(value for value in root.rglob("*") if value.is_file()):
        relative = path.relative_to(root).as_posix()
        size = path.stat().st_size
        total += size
        if len(values) >= _MAX_EXTRACTED_FILES or total > _MAX_EXTRACTED_BYTES:
            raise RuntimePackSecurityError("Runtime payload exceeds its limit.")
        values.append(
            {
                "path": relative,
                "size": size,
                "sha256": _sha256(path),
            }
        )
    return values


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _architecture() -> str:
    value = platform.machine().casefold()
    if value in {"amd64", "x86_64", "x64"}:
        return "x64"
    if value in {"arm64", "aarch64"}:
        return "arm64"
    return value


def _cuda_umd_major() -> int:
    executable = shutil.which("nvidia-smi") or shutil.which("nvidia-smi.exe")
    if not executable:
        return 0
    try:
        completed = subprocess.run(
            [executable],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0
    match = re.search(
        r"CUDA(?:\s+UMD)?\s+Version:\s*(\d+)(?:\.\d+)?",
        completed.stdout or "",
        re.IGNORECASE,
    )
    return int(match.group(1)) if match else 0


__all__ = [
    "PolishRuntimePack",
    "RuntimePackComponent",
    "RuntimePackConsentRequired",
    "RuntimePackDownloadError",
    "RuntimePackError",
    "RuntimePackManager",
    "RuntimePackNotFoundError",
    "RuntimePackSecurityError",
]
