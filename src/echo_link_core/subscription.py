from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import ssl
import tempfile
import urllib.error
import urllib.request
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .data_protection import validate_local_data_file
from .origin_client import OriginBoundClient
from .paths import data_root, resource_path


LICENSE_PATTERN = re.compile(r"^MSCR-(?:[A-Z2-9]{8}-){3}[A-Z2-9]{8}$")
MAX_RESPONSE_BYTES = 64 * 1024
STATE_MAGIC = b"MSS1"
CRYPTPROTECT_UI_FORBIDDEN = 0x1
_ENTROPY = hashlib.sha256(b"Manuscript/subscription-state/v1").digest()
PRODUCTION_BUILD_CHANNEL = "production"
TESTING_UNLOCKED_BUILD_CHANNEL = "testing-unlocked"


class SubscriptionError(RuntimeError):
    """A fail-closed subscription error safe to show to the user."""


@dataclass(frozen=True, slots=True)
class SubscriptionState:
    license_key: str
    device_secret: str

    @property
    def device_hash(self) -> str:
        return hashlib.sha256(bytes.fromhex(self.device_secret)).hexdigest()


@dataclass(frozen=True, slots=True)
class EntitlementResult:
    active: bool
    status: str
    reason: str = ""
    next_check_seconds: int = 900


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def subscription_enforcement_required(
    config_path: str | Path | None = None,
) -> bool:
    """Fail closed unless the package is explicitly marked as a local test build."""

    path = (
        Path(config_path)
        if config_path is not None
        else resource_path("resources", "subscription.json")
    )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return True
    if not isinstance(payload, dict):
        return True
    return not (
        payload.get("subscription_required") is False
        and payload.get("build_channel") == TESTING_UNLOCKED_BUILD_CHANNEL
    )


def subscription_origin(config_path: str | Path | None = None) -> str:
    path = Path(config_path) if config_path is not None else resource_path("resources", "subscription.json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        origin = str(payload["origin"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SubscriptionError("Manuscript subscription service is not configured.") from exc
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.hostname.endswith(".invalid")
    ):
        raise SubscriptionError("Manuscript subscription service is not configured.")
    return f"https://{parsed.hostname}" + (f":{parsed.port}" if parsed.port else "")


class _DenyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise SubscriptionError("The subscription service returned an unsafe redirect.")


def _validate_origin(origin: str) -> str:
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Subscription origin must be a bare HTTPS origin.")
    return f"https://{parsed.hostname}" + (f":{parsed.port}" if parsed.port else "")


class EntitlementClient(OriginBoundClient):
    _validate_origin = staticmethod(_validate_origin)

    def validate(self, license_key: str, device_hash: str, *, timeout: float = 10.0) -> EntitlementResult:
        key = normalize_license_key(license_key)
        if not re.fullmatch(r"[a-f0-9]{64}", device_hash):
            raise SubscriptionError("The local installation identity is invalid.")
        payload = self._post_json(
            "/api/license/validate",
            {"licenseKey": key, "deviceHash": device_hash},
            timeout=timeout,
        )
        active = payload.get("active") is True
        status = str(payload.get("status") or "")
        reason = str(payload.get("reason") or "")
        next_check = payload.get("nextCheckSeconds", 900)
        if isinstance(next_check, bool) or not isinstance(next_check, int):
            next_check = 900
        return EntitlementResult(active, status, reason, max(300, min(next_check, 3600)))

    def _post_json(self, path: str, payload: dict[str, str], *, timeout: float) -> dict[str, Any]:
        if path != "/api/license/validate":
            raise SubscriptionError("The subscription API path is not allowed.")
        body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.origin}{path}",
            data=body,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            if self._opener is not None:
                response_context = self._opener(request, timeout=timeout)
            else:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({}),
                    urllib.request.HTTPSHandler(context=ssl.create_default_context()),
                    _DenyRedirect(),
                )
                response_context = opener.open(request, timeout=timeout)
            with response_context as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                status = int(getattr(response, "status", response.getcode()))
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
        except urllib.error.HTTPError as exc:
            if exc.code in {400, 403}:
                try:
                    raw = exc.read(MAX_RESPONSE_BYTES + 1)
                    decoded = json.loads(raw.decode("utf-8"))
                    return decoded if isinstance(decoded, dict) else {"active": False, "reason": "invalid_license"}
                except Exception:
                    return {"active": False, "reason": "invalid_license"}
            raise SubscriptionError("The subscription service is unavailable.") from exc
        except (OSError, urllib.error.URLError, TimeoutError, SubscriptionError) as exc:
            if isinstance(exc, SubscriptionError):
                raise
            raise SubscriptionError("The subscription service is unavailable.") from exc
        if status != 200 or content_type != "application/json" or len(raw) > MAX_RESPONSE_BYTES:
            raise SubscriptionError("The subscription service returned an invalid response.")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SubscriptionError("The subscription service returned an invalid response.") from exc
        if not isinstance(decoded, dict):
            raise SubscriptionError("The subscription service returned an invalid response.")
        return decoded


class SubscriptionStateStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = validate_local_data_file(
            Path(path) if path is not None else data_root() / "manuscript-subscription.dpapi",
            purpose="Manuscript subscription state",
            allowed_suffixes={".dpapi"},
            create_parent=True,
        )

    def load(self) -> SubscriptionState | None:
        if not self.path.exists():
            return None
        try:
            raw = self.path.read_bytes()
            if len(raw) > 16_384 or not raw.startswith(STATE_MAGIC):
                raise SubscriptionError("Saved subscription state is invalid.")
            payload = json.loads(_dpapi_unprotect(raw[len(STATE_MAGIC):]).decode("utf-8"))
            state = SubscriptionState(
                normalize_license_key(str(payload["license_key"])),
                str(payload["device_secret"]),
            )
            if not re.fullmatch(r"[a-f0-9]{64}", state.device_secret):
                raise SubscriptionError("Saved subscription state is invalid.")
            return state
        except SubscriptionError:
            raise
        except Exception as exc:
            raise SubscriptionError("Saved subscription state could not be unlocked for this Windows user.") from exc

    def save(self, license_key: str, *, device_secret: str | None = None) -> SubscriptionState:
        state = SubscriptionState(
            normalize_license_key(license_key),
            device_secret or os.urandom(32).hex(),
        )
        if not re.fullmatch(r"[a-f0-9]{64}", state.device_secret):
            raise SubscriptionError("The local installation identity is invalid.")
        payload = json.dumps(
            {"license_key": state.license_key, "device_secret": state.device_secret},
            separators=(",", ":"),
        ).encode("utf-8")
        protected = STATE_MAGIC + _dpapi_protect(payload)
        handle, temporary_name = tempfile.mkstemp(prefix=".manuscript-subscription.", suffix=".tmp", dir=self.path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(protected)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        return state

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


def normalize_license_key(value: str) -> str:
    key = (value or "").strip().upper()
    if not LICENSE_PATTERN.fullmatch(key):
        raise SubscriptionError("Enter the complete Manuscript license key.")
    return key


def _blob(data: bytes) -> tuple[_DataBlob, Any]:
    buffer = ctypes.create_string_buffer(data, max(1, len(data)))
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _dpapi_protect(data: bytes) -> bytes:
    if os.name != "nt":
        raise SubscriptionError("Windows subscription protection is unavailable.")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    input_blob, input_buffer = _blob(data)
    entropy_blob, entropy_buffer = _blob(_ENTROPY)
    output_blob = _DataBlob()
    if not crypt32.CryptProtectData(
        ctypes.byref(input_blob), "Manuscript subscription", ctypes.byref(entropy_blob), None, None,
        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(output_blob),
    ):
        raise SubscriptionError("Windows could not protect the subscription state.")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        if output_blob.pbData:
            kernel32.LocalFree(output_blob.pbData)
        del input_buffer, entropy_buffer


def _dpapi_unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        raise SubscriptionError("Windows subscription protection is unavailable.")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    input_blob, input_buffer = _blob(data)
    entropy_blob, entropy_buffer = _blob(_ENTROPY)
    output_blob = _DataBlob()
    description = wintypes.LPWSTR()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(input_blob), ctypes.byref(description), ctypes.byref(entropy_blob), None, None,
        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(output_blob),
    ):
        raise SubscriptionError("Windows could not unlock the subscription state.")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        if output_blob.pbData:
            kernel32.LocalFree(output_blob.pbData)
        if description:
            kernel32.LocalFree(description)
        del input_buffer, entropy_buffer


__all__ = [
    "EntitlementClient", "EntitlementResult", "SubscriptionError", "SubscriptionState",
    "SubscriptionStateStore", "normalize_license_key",
    "subscription_enforcement_required", "subscription_origin",
]
