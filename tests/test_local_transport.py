from __future__ import annotations

import json
import socket
import urllib.request
from pathlib import Path

import pytest

import echo_link_core.local_transport as local_transport
from echo_link_core.local_transport import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    LocalTransportError,
    LocalTransportTimeout,
    LoopbackJSONTransport,
    validate_ollama_endpoint,
)
from echo_link_core.correction import OllamaRefiner
from echo_link_core.local_chat import ApprovedSourceChat
from echo_link_core.selected_text import LocalWritingAssistant
from echo_link_core.writing_models import LocalWritingModelManager


class _Response:
    def __init__(self, raw=b"{}", *, status=200, headers=None):
        self.raw = raw
        self.status = status
        self.headers = headers or {"Content-Type": "application/json"}
        self.read_limit = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit):
        self.read_limit = limit
        return self.raw


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    (
        ("http://127.0.0.1:11434", "http://127.0.0.1:11434"),
        ("http://127.0.0.1:11434/", "http://127.0.0.1:11434"),
        ("http://[::1]:11434", "http://[::1]:11434"),
        ("http://[::1]:11434/", "http://[::1]:11434"),
    ),
)
def test_only_numeric_loopback_ollama_endpoints_are_accepted(endpoint, expected):
    assert validate_ollama_endpoint(endpoint) == expected


@pytest.mark.parametrize(
    "client_type",
    (OllamaRefiner, LocalWritingAssistant, ApprovedSourceChat, LocalWritingModelManager),
)
def test_every_runtime_model_client_uses_the_strict_endpoint_gate(client_type):
    with pytest.raises(ValueError, match="numeric loopback"):
        client_type("http://localhost:11434")
    assert client_type("http://[::1]:11434").endpoint == "http://[::1]:11434"


@pytest.mark.parametrize(
    "endpoint",
    (
        "http://localhost:11434",
        "http://localhost.:11434",
        "http://127.0.0.2:11434",
        "http://127.1:11434",
        "http://0.0.0.0:11434",
        "https://127.0.0.1:11434",
        "http://127.0.0.1",
        "http://127.0.0.1:80",
        "http://127.0.0.1:11434/api",
        "http://user@127.0.0.1:11434",
        "http://user:pass@127.0.0.1:11434",
        "http://127.0.0.1:11434?",
        "http://127.0.0.1:11434?x=1",
        "http://127.0.0.1:11434#",
        "http://127.0.0.1:11434#part",
        "http://[::1%25zone]:11434",
        " http://127.0.0.1:11434",
        "http://127.0.0.1:11434 ",
        "",
    ),
)
def test_noncanonical_or_nonloopback_endpoints_are_rejected(endpoint):
    with pytest.raises(ValueError, match="numeric loopback"):
        validate_ollama_endpoint(endpoint)


def test_invalid_endpoint_path_timeout_and_body_fail_before_open(monkeypatch):
    calls = []
    monkeypatch.setattr(
        local_transport,
        "_open_request",
        lambda *_args, **_kwargs: calls.append(True),
    )
    with pytest.raises(ValueError):
        LoopbackJSONTransport("http://example.com:11434")

    transport = LoopbackJSONTransport("http://127.0.0.1:11434")
    for path in (
        "api/tags",
        "/api/tags?all=true",
        "//example.com/api/tags",
        "/api/unknown",
    ):
        with pytest.raises(LocalTransportError, match="path"):
            transport.get_json(path, timeout=1.0)
    for timeout in (0, 0.19, 301, float("inf"), float("nan"), True):
        with pytest.raises(LocalTransportError, match="timeout"):
            transport.get_json("/api/tags", timeout=timeout)
    with pytest.raises(LocalTransportError, match="too large"):
        transport.post_json(
            "/api/generate",
            {"prompt": "x" * (MAX_REQUEST_BYTES + 1)},
            timeout=1.0,
        )
    assert calls == []


def test_poisoned_proxy_environment_is_ignored(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://example.invalid:8888")
    monkeypatch.setenv("HTTPS_PROXY", "http://example.invalid:8888")
    captured = {}
    response = _Response(json.dumps({"models": []}).encode("utf-8"))

    class _Opener:
        def open(self, request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return response

    def fake_build_opener(*handlers):
        captured["handlers"] = handlers
        return _Opener()

    monkeypatch.setattr(urllib.request, "build_opener", fake_build_opener)
    result = LoopbackJSONTransport("http://127.0.0.1:11434").get_json(
        "/api/tags",
        timeout=1.5,
    )
    assert result == {"models": []}
    proxy = next(
        handler
        for handler in captured["handlers"]
        if isinstance(handler, urllib.request.ProxyHandler)
    )
    assert proxy.proxies == {}
    assert any(
        isinstance(handler, local_transport._DenyRedirectHandler)
        for handler in captured["handlers"]
    )
    assert captured["request"].full_url == "http://127.0.0.1:11434/api/tags"
    assert captured["timeout"] == 1.5


def test_redirect_handler_denies_every_redirect():
    handler = local_transport._DenyRedirectHandler()
    with pytest.raises(LocalTransportError, match="redirects"):
        handler.redirect_request(None, None, 302, "Found", {}, "https://example.com")


@pytest.mark.parametrize(
    "response",
    (
        _Response(b"{}", status=201),
        _Response(b"{}", headers={"Content-Type": "text/plain"}),
        _Response(
            b"{}",
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(MAX_RESPONSE_BYTES + 1),
            },
        ),
        _Response(b"[1,2,3]"),
        _Response(b"not-json"),
        _Response(b"\xff"),
    ),
)
def test_response_status_headers_encoding_and_shape_are_bounded(monkeypatch, response):
    monkeypatch.setattr(local_transport, "_open_request", lambda *_args: response)
    with pytest.raises(LocalTransportError):
        LoopbackJSONTransport("http://127.0.0.1:11434").get_json(
            "/api/tags",
            timeout=1.0,
        )


def test_response_body_read_is_hard_bounded(monkeypatch):
    response = _Response(b"x" * (MAX_RESPONSE_BYTES + 1))
    monkeypatch.setattr(local_transport, "_open_request", lambda *_args: response)
    with pytest.raises(LocalTransportError, match="too large"):
        LoopbackJSONTransport("http://127.0.0.1:11434").get_json(
            "/api/tags",
            timeout=1.0,
        )
    assert response.read_limit == MAX_RESPONSE_BYTES + 1


def test_post_uses_only_json_and_returns_object(monkeypatch):
    captured = {}

    def fake_open(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return _Response(b'{"response":"local"}')

    monkeypatch.setattr(local_transport, "_open_request", fake_open)
    payload = LoopbackJSONTransport("http://[::1]:11434").post_json(
        "/api/generate",
        {"model": "local", "stream": False},
        timeout=2.0,
    )
    assert payload == {"response": "local"}
    request = captured["request"]
    assert request.full_url == "http://[::1]:11434/api/generate"
    assert request.method == "POST"
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("Accept") == "application/json"
    assert json.loads(request.data.decode("utf-8")) == {"model": "local", "stream": False}


def test_transport_reports_timeout_instead_of_service_unavailable(monkeypatch):
    def timed_out(*_args):
        raise socket.timeout("timed out")

    monkeypatch.setattr(local_transport, "_open_request", timed_out)
    with pytest.raises(LocalTransportTimeout, match="timed out"):
        LoopbackJSONTransport("http://127.0.0.1:11434").get_json(
            "/api/ps",
            timeout=1.0,
        )


def test_runtime_model_clients_have_no_direct_http_calls_left():
    root = Path(__file__).resolve().parents[1] / "src" / "echo_link_core"
    for name in ("correction.py", "selected_text.py", "local_chat.py", "writing_models.py"):
        source = (root / name).read_text(encoding="utf-8")
        assert "urllib" not in source
        assert "urlopen" not in source
        assert "LoopbackJSONTransport" in source
