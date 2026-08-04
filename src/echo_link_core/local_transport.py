from __future__ import annotations

import json
import math
import numbers
import socket
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit


OLLAMA_PORTS = frozenset({11434})
OLLAMA_PATHS = frozenset({"/api/generate", "/api/ps", "/api/tags", "/api/version"})
MAX_REQUEST_BYTES = 4 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MIN_TIMEOUT_SECONDS = 0.2
MAX_TIMEOUT_SECONDS = 300.0
_NUMERIC_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})


class LocalTransportError(RuntimeError):
    """A fail-closed local transport error safe to show to a user."""


class LocalTransportTimeout(LocalTransportError):
    """The loopback service accepted work but did not finish in time."""


class LocalTransportConnectionError(LocalTransportError):
    """The loopback service could not be reached."""


class LocalTransportResponseError(LocalTransportError):
    """The loopback service returned malformed or unsafe data."""


class _DenyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise LocalTransportError("Local model redirects are not allowed.")


class LoopbackJSONTransport:
    """Strict JSON transport for a numeric loopback Ollama service."""

    def __init__(self, endpoint: str) -> None:
        self._endpoint = validate_ollama_endpoint(endpoint)

    @property
    def endpoint(self) -> str:
        return self._endpoint

    def get_json(self, path: str, *, timeout: float) -> dict[str, Any]:
        return self.request_json(path, method="GET", timeout=timeout)

    def post_json(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise LocalTransportError("Local model request payload must be an object.")
        try:
            body = json.dumps(
                dict(payload),
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError) as exc:
            raise LocalTransportError("Local model request payload is invalid.") from exc
        if len(body) > MAX_REQUEST_BYTES:
            raise LocalTransportError("Local model request is too large.")
        return self.request_json(path, method="POST", body=body, timeout=timeout)

    def request_json(
        self,
        path: str,
        *,
        method: str,
        timeout: float,
        body: bytes | None = None,
    ) -> dict[str, Any]:
        url = self._build_url(path)
        bounded_timeout = _validate_timeout(timeout)
        if method not in {"GET", "POST"}:
            raise LocalTransportError("Local model request method is not allowed.")
        if method == "GET" and body is not None:
            raise LocalTransportError("Local model GET request cannot contain a body.")
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Accept": "application/json",
                **({"Content-Type": "application/json"} if body is not None else {}),
            },
            method=method,
        )
        try:
            with _open_request(request, bounded_timeout) as response:
                _validate_response_metadata(response)
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except LocalTransportError:
            raise
        except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            if _is_timeout_error(exc):
                raise LocalTransportTimeout(
                    f"Local model request timed out after {bounded_timeout:.1f} seconds."
                ) from exc
            raise LocalTransportConnectionError(
                "Local model service could not be reached."
            ) from exc
        if not isinstance(raw, bytes):
            raise LocalTransportError("Local model response was not bytes.")
        if len(raw) > MAX_RESPONSE_BYTES:
            raise LocalTransportError("Local model response is too large.")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LocalTransportResponseError(
                "Local model response was not valid UTF-8 JSON."
            ) from exc
        if not isinstance(payload, dict):
            raise LocalTransportResponseError(
                "Local model response must be a JSON object."
            )
        return payload

    def _build_url(self, path: str) -> str:
        if not isinstance(path, str) or path not in OLLAMA_PATHS:
            raise LocalTransportError("Local model API path is not allowed.")
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            raise LocalTransportError("Local model API path is not allowed.")
        return f"{self._endpoint}{path}"


def validate_ollama_endpoint(endpoint: str) -> str:
    if not isinstance(endpoint, str) or not endpoint or endpoint != endpoint.strip():
        raise ValueError("Local model access requires a numeric loopback Ollama endpoint.")
    parsed = urlsplit(endpoint)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(
            "Local model access requires a numeric loopback Ollama endpoint."
        ) from exc
    host = parsed.hostname or ""
    if (
        parsed.scheme != "http"
        or host not in _NUMERIC_LOOPBACK_HOSTS
        or port not in OLLAMA_PORTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or "?" in endpoint
        or "#" in endpoint
    ):
        raise ValueError("Local model access requires a numeric loopback Ollama endpoint.")
    canonical_host = "[::1]" if host == "::1" else "127.0.0.1"
    return "http:" + f"//{canonical_host}:{port}"


def _validate_timeout(timeout: float) -> float:
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, numbers.Real)
        or not math.isfinite(float(timeout))
        or not MIN_TIMEOUT_SECONDS <= float(timeout) <= MAX_TIMEOUT_SECONDS
    ):
        raise LocalTransportError("Local model timeout is outside the allowed range.")
    return float(timeout)


def _validate_response_metadata(response) -> None:
    status = getattr(response, "status", None)
    if status is None and hasattr(response, "getcode"):
        status = response.getcode()
    if status is not None and int(status) != 200:
        raise LocalTransportError("Local model service returned an unexpected status.")
    headers = getattr(response, "headers", None)
    if headers is None:
        return
    content_length = headers.get("Content-Length")
    if content_length:
        try:
            length = int(content_length)
        except ValueError as exc:
            raise LocalTransportError("Local model response length is invalid.") from exc
        if length < 0 or length > MAX_RESPONSE_BYTES:
            raise LocalTransportError("Local model response is too large.")
    content_type = headers.get("Content-Type")
    if content_type and content_type.split(";", 1)[0].strip().casefold() != "application/json":
        raise LocalTransportError("Local model response content type is not JSON.")


def _open_request(request: urllib.request.Request, timeout: float):
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _DenyRedirectHandler(),
    )
    return opener.open(request, timeout=timeout)


def _is_timeout_error(error: BaseException) -> bool:
    if isinstance(error, (TimeoutError, socket.timeout)):
        return True
    reason = getattr(error, "reason", None)
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return True
    return "timed out" in str(error).casefold() or "timeout" in str(error).casefold()
