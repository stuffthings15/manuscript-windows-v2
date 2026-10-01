from __future__ import annotations

import importlib
import base64
import hashlib
import io
import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).parents[1]


def _module():
    try:
        return importlib.import_module("echo_link_core.browser_native_host")
    except ModuleNotFoundError:
        pytest.fail("The browser Native Messaging host module is missing.")


def _frame(payload: dict[str, object]) -> bytes:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return struct.pack("<I", len(raw)) + raw


def test_native_message_framing_round_trips_one_bounded_json_object():
    host = _module()
    payload = {"id": "request-1", "type": "analyze", "text": "This are wrong."}
    stream = io.BytesIO(_frame(payload))

    assert host.read_native_message(stream) == payload

    output = io.BytesIO()
    host.write_native_message(output, {"id": "request-1", "ok": True})
    size = struct.unpack("<I", output.getvalue()[:4])[0]
    assert json.loads(output.getvalue()[4 : 4 + size]) == {
        "id": "request-1",
        "ok": True,
    }


def test_native_message_rejects_oversized_or_invalid_frames():
    host = _module()
    with pytest.raises(host.NativeMessageError, match="size"):
        host.read_native_message(io.BytesIO(struct.pack("<I", 1_048_577)))
    with pytest.raises(host.NativeMessageError, match="JSON"):
        host.read_native_message(io.BytesIO(struct.pack("<I", 1) + b"{"))


def test_browser_host_requires_active_subscription_before_correction():
    host = _module()
    engine_used = False

    def engine_factory():
        nonlocal engine_used
        engine_used = True
        raise AssertionError("Correction engine must stay locked.")

    bridge = host.BrowserNativeHost(
        engine_factory=engine_factory,
        subscription_gate=lambda: False,
    )

    response = bridge.handle(
        {
            "id": "request-2",
            "type": "analyze",
            "text": "This are wrong.",
            "command": "Proofread",
        }
    )

    assert response == {
        "id": "request-2",
        "ok": False,
        "error": "active_subscription_required",
    }
    assert engine_used is False


def test_browser_host_returns_review_only_correction_and_minimal_edits():
    host = _module()
    request_seen = None

    class Engine:
        def analyze(self, request):
            nonlocal request_seen
            request_seen = request
            return SimpleNamespace(
                text="This is wrong.",
                edits=(
                    SimpleNamespace(
                        operation="replace",
                        original="are",
                        replacement="is",
                        original_start=5,
                        original_end=8,
                    ),
                ),
                message="Local correction ready.",
                plan=SimpleNamespace(status_label="Rules + MiniLM"),
                used_llm=False,
            )

    bridge = host.BrowserNativeHost(
        engine_factory=Engine,
        subscription_gate=lambda: True,
    )
    response = bridge.handle(
        {
            "id": "request-3",
            "type": "analyze",
            "text": "This are wrong.",
            "command": "Proofread",
            "profile": "General",
            "mode": "instant",
        }
    )

    assert request_seen.text == "This are wrong."
    assert request_seen.command == "Proofread"
    assert response == {
        "id": "request-3",
        "ok": True,
        "text": "This is wrong.",
        "edits": [
            {
                "operation": "replace",
                "original": "are",
                "replacement": "is",
                "original_start": 5,
                "original_end": 8,
            }
        ],
        "message": "Local correction ready.",
        "backend": "Rules + MiniLM",
        "used_llm": False,
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"id": "x", "type": "unknown", "text": "Text"},
        {"id": "x", "type": "analyze", "text": ""},
        {"id": "x", "type": "analyze", "text": "x" * 500_001},
        {"id": "x", "type": "analyze", "text": "Text", "command": "Delete files"},
        {"id": "../bad", "type": "analyze", "text": "Text"},
    ],
)
def test_browser_host_rejects_untrusted_request_shapes(payload):
    host = _module()
    bridge = host.BrowserNativeHost(
        engine_factory=lambda: pytest.fail("Invalid requests must not reach the engine."),
        subscription_gate=lambda: True,
    )

    response = bridge.handle(payload)

    assert response["ok"] is False
    assert response["error"] == "invalid_request"


def test_extension_uses_native_messaging_without_network_or_silent_apply():
    extension = ROOT / "browser-extension"
    manifest = json.loads((extension / "manifest.json").read_text(encoding="utf-8"))
    background = (extension / "background.js").read_text(encoding="utf-8")
    content = (extension / "content.js").read_text(encoding="utf-8")
    combined = (background + "\n" + content).casefold()

    assert manifest["manifest_version"] == 3
    assert manifest["permissions"] == ["nativeMessaging"]
    assert "host_permissions" not in manifest
    assert "sendNativeMessage" in background
    assert "fetch(" not in combined
    assert "xmlhttprequest" not in combined
    assert "websocket" not in combined
    assert 'type === "password"' in content
    assert "Replace" in content
    assert "currentText" in content
    assert "dispatchEvent" in content


def test_extension_identity_matches_its_committed_public_key():
    extension = ROOT / "browser-extension"
    manifest = json.loads((extension / "manifest.json").read_text(encoding="utf-8"))
    raw_key = base64.b64decode(manifest["key"], validate=True)
    digest = hashlib.sha256(raw_key).digest()[:16].hex()
    expected_id = "".join(chr(ord("a") + int(value, 16)) for value in digest)

    assert (extension / "extension-id.txt").read_text(encoding="ascii").strip() == expected_id


def test_build_and_installer_package_and_register_browser_native_host():
    browser_spec = (ROOT / "BrowserHost.spec").read_text(encoding="utf-8")
    build = (ROOT / "tools" / "build.py").read_text(encoding="utf-8")
    installer = (ROOT / "packaging" / "EchoLink.iss").read_text(encoding="utf-8")
    installer_tool = (
        ROOT / "tools" / "install_browser_native_host.py"
    ).read_text(encoding="utf-8")

    assert "run_browser_host.py" in browser_spec
    assert 'name="ManuscriptV2BrowserHost"' in browser_spec
    assert "console=True" in browser_spec
    assert '"BrowserHost.spec"' in build
    assert '"browser-host"' in build
    assert '"browser-extension"' in build
    assert "NativeMessagingHosts\\com.manuscript.v2.polish" in installer
    # HKLM, not HKCU: the installer is machine-wide
    # (PrivilegesRequired=admin), so a host registered in one user's hive
    # would leave every other user on the machine with an extension that
    # silently does nothing.
    assert 'Root: HKLM; Subkey: "Software\\Google\\Chrome' in installer
    assert 'Root: HKLM; Subkey: "Software\\Microsoft\\Edge' in installer
    assert "ManuscriptV2BrowserHost.exe" in installer
    assert "allowed_origins" in installer
    assert "HKEY_CURRENT_USER" in installer_tool
    assert "Google\\Chrome\\NativeMessagingHosts" in installer_tool
    assert "Microsoft\\Edge\\NativeMessagingHosts" in installer_tool
