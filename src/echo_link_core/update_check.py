"""Check whether a newer Manuscript release exists.

This is the only network call in the product that is not entitlement
verification, so it inherits the same fail-closed shape as
``subscription.py`` rather than inventing a second one: a bare-HTTPS origin,
a single allowed path, a denied redirect, a byte cap, and a strict response
shape. Nothing here is wired into the correction path, and nothing here
sends the user's text, document names, or anything beyond the version
already visible in the title bar.

The check is opt-in. ``check_now`` is what a menu action calls. Nothing in
this module schedules itself; a caller that wants a periodic check must ask
for one explicitly, so "no update UI wired up yet" cannot silently start
making requests no one asked for.
"""

from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .origin_client import OriginBoundClient
from .version import PRODUCT_VERSION


MAX_RESPONSE_BYTES = 16 * 1024
_UPDATE_PATH = "/api/updates/latest"
_VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class UpdateCheckError(RuntimeError):
    """A fail-closed update-check error safe to show to the user."""


@dataclass(frozen=True, slots=True)
class UpdateInfo:
    available: bool
    version: str = ""
    download_url: str = ""
    sha256: str = ""
    notes: str = ""


def _version_tuple(value: str) -> tuple[int, int, int]:
    major, minor, patch = value.split(".")
    return (int(major), int(minor), int(patch))


def _is_newer(candidate: str, current: str) -> bool:
    try:
        return _version_tuple(candidate) > _version_tuple(current)
    except (ValueError, AttributeError):
        return False


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
        or parsed.hostname.endswith(".invalid")
    ):
        raise UpdateCheckError("The update service is not configured.")
    return f"https://{parsed.hostname}" + (f":{parsed.port}" if parsed.port else "")


def _validate_download_url(url: str, *, origin: str) -> str:
    try:
        parsed = urlsplit(url)
        expected = urlsplit(origin)
        # A matching hostname alone still permits a different server port.
        same_origin = (
            parsed.hostname == expected.hostname
            and (parsed.port or 443) == (expected.port or 443)
        )
    except ValueError as exc:
        raise UpdateCheckError("The update service returned an unsafe download location.") from exc
    if (
        parsed.scheme != "https"
        or not same_origin
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise UpdateCheckError("The update service returned an unsafe download location.")
    return url


class _DenyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise UpdateCheckError("The update service returned an unsafe redirect.")


class UpdateClient(OriginBoundClient):
    """Ask one fixed origin, at one fixed path, whether a newer build exists."""

    _validate_origin = staticmethod(_validate_origin)

    def check(self, *, current_version: str = PRODUCT_VERSION, timeout: float = 10.0) -> UpdateInfo:
        if not _VERSION_PATTERN.fullmatch(current_version):
            raise UpdateCheckError("The installed version could not be read.")
        payload = self._get_json(timeout=timeout)
        candidate = str(payload.get("version") or "")
        if not candidate or not _VERSION_PATTERN.fullmatch(candidate):
            return UpdateInfo(available=False)
        if not _is_newer(candidate, current_version):
            return UpdateInfo(available=False)
        download_url = _validate_download_url(str(payload.get("downloadUrl") or ""), origin=self.origin)
        sha256 = str(payload.get("sha256") or "").strip().lower()
        if not _SHA256_PATTERN.fullmatch(sha256):
            raise UpdateCheckError("The update service did not provide a verifiable download.")
        notes = str(payload.get("notes") or "")[:2000]
        return UpdateInfo(
            available=True,
            version=candidate,
            download_url=download_url,
            sha256=sha256,
            notes=notes,
        )

    def _get_json(self, *, timeout: float) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.origin}{_UPDATE_PATH}",
            headers={"Accept": "application/json"},
            method="GET",
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
        except (OSError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError, UpdateCheckError) as exc:
            if isinstance(exc, UpdateCheckError):
                raise
            raise UpdateCheckError("The update service is unavailable.") from exc
        if status != 200 or content_type != "application/json" or len(raw) > MAX_RESPONSE_BYTES:
            raise UpdateCheckError("The update service returned an invalid response.")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpdateCheckError("The update service returned an invalid response.") from exc
        if not isinstance(decoded, dict):
            raise UpdateCheckError("The update service returned an invalid response.")
        return decoded


def check_now(origin: str, *, current_version: str = PRODUCT_VERSION) -> UpdateInfo:
    """Ask once. Never called automatically; a caller must ask for this."""

    return UpdateClient(origin).check(current_version=current_version)


__all__ = [
    "UpdateCheckError",
    "UpdateClient",
    "UpdateInfo",
    "check_now",
]
