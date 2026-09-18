from __future__ import annotations

import argparse
import hashlib
import ipaddress
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
CANONICAL_MARKER = "manuscript-windows-v2-canonical-v1"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Build the standalone Manuscript app")
    result.add_argument("--clean", action="store_true")
    result.add_argument("--installer", action="store_true")
    result.add_argument("--dry-run", action="store_true")
    result.add_argument(
        "--testing-unlocked",
        action="store_true",
        help="Build an obvious local test package with subscription enforcement disabled",
    )
    result.add_argument(
        "--signing-thumbprint",
        default="",
        help=(
            "SHA-1 thumbprint of the trusted Windows code-signing certificate. "
            "Production builds require this or MANUSCRIPT_SIGNING_THUMBPRINT."
        ),
    )
    result.add_argument(
        "--azure-signing-metadata",
        default="",
        help=(
            "Path to an Azure Trusted Signing metadata.json. Overrides the "
            "MANUSCRIPT_AZURE_SIGNING_METADATA environment variable. Requires "
            "MANUSCRIPT_AZURE_SIGNING_DLIB to point at Azure.CodeSigning.Dlib.dll."
        ),
    )
    result.add_argument(
        "--release-download-base-url",
        default="",
        help=(
            "Public HTTPS origin the built installer will be uploaded under, e.g. "
            "https://releases.example.com. Overrides "
            "MANUSCRIPT_RELEASE_DOWNLOAD_BASE_URL. Required with --installer "
            "unless --testing-unlocked; used to write installer-output/"
            "release-manifest.json for echo_link_core.update_check."
        ),
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    _assert_canonical_project_root(ROOT)
    missing = [
        module
        for module in (
            "sqlcipher3",
            "onnxruntime",
            "symspellpy",
            "tokenizers",
            "uiautomation",
            "winrt.runtime",
            "winrt.windows.foundation",
            "winrt.windows.foundation.collections",
            "winrt.windows.media.control",
        )
        if importlib.util.find_spec(module) is None
    ]
    if missing:
        raise SystemExit(
            "Build dependency missing: "
            + ", ".join(missing)
            + ". Install requirements.txt before packaging."
        )
    model = ROOT / "models" / "base.en" / "model.bin"
    if not model.is_file():
        raise SystemExit("Bundled speech model is missing: models\\base.en\\model.bin")
    minilm = (
        ROOT
        / "models"
        / "polish"
        / "minilm-l6-v2-int8"
        / "onnx"
        / "model_quint8_avx2.onnx"
    )
    if not minilm.is_file():
        raise SystemExit("Bundled MiniLM classifier is missing.")
    from echo_link_core.polish_model_packs import ModelPackManager

    ModelPackManager(
        install_root=ROOT / "build" / "polish-pack-check",
    ).catalog(refresh_installed=False)

    pyinstaller = [sys.executable, "-m", "PyInstaller", str(ROOT / "EchoLink.spec"), "--noconfirm"]
    browser_pyinstaller = [
        sys.executable,
        "-m",
        "PyInstaller",
        str(ROOT / "BrowserHost.spec"),
        "--noconfirm",
    ]
    if args.clean:
        pyinstaller.append("--clean")
        browser_pyinstaller.append("--clean")
    if args.dry_run:
        subprocess.run(
            [sys.executable, str(ROOT / "tools" / "build_polish_host.py"), "--dry-run"],
            cwd=ROOT,
            check=True,
        )
        print(" ".join(browser_pyinstaller))
        print(" ".join(pyinstaller))
        print(
            "build channel: "
            + ("testing-unlocked" if args.testing_unlocked else "production")
        )
        if args.installer:
            print(str(ROOT / "packaging" / "EchoLink.iss"))
        return 0

    release_download_base_url = ""
    if args.installer:
        release_download_base_url = _release_download_base_url(
            args.release_download_base_url,
            testing_unlocked=args.testing_unlocked,
            environ=os.environ,
        )
    signing = _resolve_signing(args, os.environ)
    signtool = _find_signtool() if signing else None
    if signing and signtool is None:
        raise SystemExit(
            "A code-signing configuration was selected, but SignTool.exe "
            "was not found."
        )

    _write_build_info(testing_unlocked=args.testing_unlocked)
    _write_subscription_manifest(testing_unlocked=args.testing_unlocked)
    subprocess.run(
        [sys.executable, str(ROOT / "tools" / "build_polish_host.py")],
        cwd=ROOT,
        check=True,
    )
    if signing and signtool:
        _sign_files(
            _signable_files(ROOT / "build" / "generated" / "polish-host"),
            signing,
            signtool,
        )
    if args.testing_unlocked:
        print(
            "WARNING: building a TESTING UNLOCKED package. "
            "Do not publish or sell this artifact."
        )
    subprocess.run(browser_pyinstaller, cwd=ROOT, check=True)
    subprocess.run(pyinstaller, cwd=ROOT, check=True)
    executable = ROOT / "dist" / "ManuscriptV2" / "ManuscriptV2.exe"
    if not executable.is_file():
        raise SystemExit(f"Build did not create {executable}")
    _copy_browser_integration()
    if signing and signtool:
        _sign_files(
            _signable_files(ROOT / "dist" / "ManuscriptV2"),
            signing,
            signtool,
        )

    if args.installer:
        iscc = _find_iscc()
        if iscc is None:
            raise SystemExit("Manuscript built, but Inno Setup 6 was not found.")
        installer_stem = (
            "ManuscriptV2TestingSetup" if args.testing_unlocked else "ManuscriptV2Setup"
        )
        subprocess.run(
            [
                str(iscc),
                f"/F{installer_stem}",
                str(ROOT / "packaging" / "EchoLink.iss"),
            ],
            cwd=ROOT,
            check=True,
        )
        installer = ROOT / "installer-output" / f"{installer_stem}.exe"
        if not installer.is_file():
            raise SystemExit(f"Build did not create {installer}")
        if signing and signtool:
            _sign_files([installer], signing, signtool)
        # After signing, not before: the sha256 published here must match the
        # exact bytes a writer downloads and update_check.py verifies against.
        manifest = _write_release_manifest(
            installer,
            testing_unlocked=args.testing_unlocked,
            download_base_url=release_download_base_url,
        )
        print(manifest)
        print(installer)
    print(executable)
    return 0


def _assert_canonical_project_root(root: Path) -> None:
    marker = root / ".manuscript-canonical-root"
    try:
        marker_value = marker.read_text(encoding="utf-8").strip()
    except OSError:
        marker_value = ""
    if (
        marker_value != CANONICAL_MARKER
        or root.name.casefold() != "windows version 2"
        or root.parent.name.casefold() != "manuscript"
        or not (root / "src" / "echo_link_core" / "polish.py").is_file()
    ):
        raise SystemExit(
            "Build refused. Use the canonical Manuscript/Windows Version 2 project root."
        )


def _copy_browser_integration() -> None:
    app_root = ROOT / "dist" / "ManuscriptV2"
    host_source = ROOT / "dist" / "ManuscriptV2BrowserHost"
    host_executable = host_source / "ManuscriptV2BrowserHost.exe"
    if not host_executable.is_file():
        raise SystemExit(f"Build did not create {host_executable}")
    host_target = app_root / "browser-host"
    extension_target = app_root / "browser-extension"
    shutil.rmtree(host_target, ignore_errors=True)
    shutil.rmtree(extension_target, ignore_errors=True)
    shutil.copytree(host_source, host_target)
    shutil.copytree(ROOT / "browser-extension", extension_target)


def _find_iscc() -> Path | None:
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    ]
    command = shutil.which("ISCC.exe")
    if command:
        candidates.insert(0, Path(command))
    return next((path for path in candidates if path.is_file()), None)


def _release_signing_thumbprint(
    value: str,
    *,
    testing_unlocked: bool,
    environ: Mapping[str, str],
) -> str | None:
    supplied = value or environ.get("MANUSCRIPT_SIGNING_THUMBPRINT", "")
    normalized = re.sub(r"\s+", "", supplied).upper()
    if not normalized:
        if testing_unlocked:
            return None
        raise SystemExit(
            "Production builds require a trusted code-signing certificate. "
            "Pass --signing-thumbprint or set MANUSCRIPT_SIGNING_THUMBPRINT."
        )
    if not re.fullmatch(r"[0-9A-F]{40}", normalized):
        raise SystemExit(
            "The trusted code-signing certificate thumbprint must contain "
            "exactly 40 hexadecimal characters."
        )
    return normalized


def _find_signtool() -> Path | None:
    command = shutil.which("signtool.exe")
    if command:
        return Path(command)
    kits_root = Path(r"C:\Program Files (x86)\Windows Kits\10\bin")
    candidates = sorted(
        kits_root.glob(r"*\x64\signtool.exe"),
        reverse=True,
    )
    return next((path for path in candidates if path.is_file()), None)


def _signable_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    extensions = {".dll", ".exe", ".pyd"}
    return sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in extensions
        ),
        key=lambda path: (
            len(path.relative_to(root).parts),
            path.relative_to(root).as_posix().lower(),
        ),
    )


def _resolve_signing(args, environ) -> tuple | None:
    """Pick the signing mode: Azure Trusted Signing wins, then thumbprint."""
    metadata = (
        str(getattr(args, "azure_signing_metadata", "") or "").strip()
        or environ.get("MANUSCRIPT_AZURE_SIGNING_METADATA", "").strip()
    )
    if metadata:
        dlib = environ.get("MANUSCRIPT_AZURE_SIGNING_DLIB", "").strip()
        metadata_path = Path(metadata)
        if not metadata_path.is_file():
            raise SystemExit(
                f"Azure Trusted Signing metadata file is missing: {metadata_path}"
            )
        if not dlib or not Path(dlib).is_file():
            raise SystemExit(
                "MANUSCRIPT_AZURE_SIGNING_DLIB must point at "
                "Azure.CodeSigning.Dlib.dll from the Trusted Signing client."
            )
        return ("azure", Path(dlib), metadata_path)
    thumbprint = _release_signing_thumbprint(
        args.signing_thumbprint,
        testing_unlocked=args.testing_unlocked,
        environ=environ,
    )
    if thumbprint:
        return ("sha1", thumbprint)
    return None


def _sign_command(tool: Path, signing, artifact: Path) -> list[str]:
    # A bare thumbprint string keeps the historical call contract working.
    if isinstance(signing, str):
        signing = ("sha1", signing)
    if signing[0] == "azure":
        _mode, dlib, metadata = signing
        return [
            str(tool),
            "sign",
            "/v",
            "/fd",
            "SHA256",
            "/td",
            "SHA256",
            "/tr",
            "http://timestamp.acs.microsoft.com",
            "/dlib",
            str(dlib),
            "/dmdf",
            str(metadata),
            str(artifact),
        ]
    _mode, thumbprint = signing
    return [
        str(tool),
        "sign",
        "/sha1",
        thumbprint,
        "/fd",
        "SHA256",
        "/td",
        "SHA256",
        "/tr",
        "https://timestamp.digicert.com",
        str(artifact),
    ]


def _verify_signature_command(tool: Path, artifact: Path) -> list[str]:
    return [str(tool), "verify", "/pa", "/all", str(artifact)]


def _sign_files(
    artifacts: list[Path],
    signing: tuple,
    signtool: Path,
) -> None:
    if not artifacts:
        raise SystemExit("No Windows binaries were found to sign.")
    for artifact in artifacts:
        subprocess.run(
            _sign_command(signtool, signing, artifact),
            cwd=ROOT,
            check=True,
        )
        subprocess.run(
            _verify_signature_command(signtool, artifact),
            cwd=ROOT,
            check=True,
        )


def _write_build_info(*, testing_unlocked: bool = False) -> None:
    head = _read_build_commit(ROOT)
    target = ROOT / "build" / "generated" / "build_info.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "commit": head[:12],
                "build_channel": (
                    "testing-unlocked" if testing_unlocked else "production"
                ),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_subscription_manifest(*, testing_unlocked: bool = False) -> Path:
    source = ROOT / "resources" / "subscription.json"
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
        origin = payload["origin"]
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit("Source subscription configuration is invalid.") from exc
    if not isinstance(origin, str) or not origin.strip():
        raise SystemExit("Source subscription configuration is invalid.")
    origin = origin.strip()
    if not testing_unlocked and not _is_public_https_origin(origin):
        raise SystemExit(
            "Production builds require a public HTTPS subscription origin."
        )
    target = ROOT / "build" / "generated" / "subscription.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "origin": origin,
                "subscription_required": not testing_unlocked,
                "build_channel": (
                    "testing-unlocked" if testing_unlocked else "production"
                ),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return target


def _release_download_base_url(value: str, *, testing_unlocked: bool, environ: Mapping[str, str]) -> str:
    supplied = (value or environ.get("MANUSCRIPT_RELEASE_DOWNLOAD_BASE_URL", "")).strip()
    if not supplied:
        if testing_unlocked:
            return ""
        raise SystemExit(
            "Production installers require a public HTTPS release download base URL. "
            "Pass --release-download-base-url or set MANUSCRIPT_RELEASE_DOWNLOAD_BASE_URL."
        )
    if not _is_public_https_origin(supplied):
        raise SystemExit(
            "The release download base URL must be a public HTTPS origin, no path."
        )
    return supplied


def _write_release_manifest(
    installer: Path,
    *,
    testing_unlocked: bool,
    download_base_url: str,
) -> Path:
    # update_check.UpdateClient pins a download URL to the same host and port
    # as the update-check origin (see _validate_download_url), so this manifest
    # must publish a URL under that same origin - a fixed /releases/<file> path
    # keeps that a promise this function can enforce rather than a convention
    # a release script could get wrong.
    from echo_link_core.version import PRODUCT_VERSION

    if not re.fullmatch(r"\d+\.\d+\.\d+", PRODUCT_VERSION):
        raise SystemExit(
            "PRODUCT_VERSION must be MAJOR.MINOR.PATCH to publish a release manifest."
        )
    sha256 = hashlib.sha256(installer.read_bytes()).hexdigest()
    base = download_base_url.strip()
    download_url = (
        f"{base}/releases/{installer.name}"
        if base
        else f"https://replace-before-release.invalid/releases/{installer.name}"
    )
    target = installer.parent / "release-manifest.json"
    target.write_text(
        json.dumps(
            {
                "version": PRODUCT_VERSION,
                "downloadUrl": download_url,
                "sha256": sha256,
                "notes": "",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return target


def _is_public_https_origin(origin: str) -> bool:
    parsed = urlsplit(origin)
    hostname = (parsed.hostname or "").casefold()
    try:
        parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or hostname.endswith(".")
        or hostname in {"localhost", "localhost.localdomain"}
        or hostname.endswith((".localhost", ".local", ".invalid", ".test"))
    ):
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return bool(
            re.fullmatch(
                r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
                r"[a-z]{2,63}",
                hostname,
            )
        )
    return address.is_global

def _read_build_commit(root: Path) -> str:
    override = os.environ.get("MANUSCRIPT_BUILD_COMMIT", "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{12,40}", override):
        return override.lower()
    for repository in (root, *root.parents):
        git_dir = repository / ".git"
        head_path = git_dir / "HEAD"
        if not head_path.is_file():
            continue
        head = head_path.read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            ref_path = git_dir / head[5:]
            if not ref_path.is_file():
                continue
            head = ref_path.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"[0-9a-fA-F]{40}", head):
            return head.lower()
    raise SystemExit(
        "Build commit is unavailable. Run from a Git checkout or set "
        "MANUSCRIPT_BUILD_COMMIT to the verified commit hash."
    )


if __name__ == "__main__":
    raise SystemExit(main())
