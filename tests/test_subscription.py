from __future__ import annotations

import json

import pytest

from echo_link_app import main as app_main
from echo_link_core.subscription import (
    EntitlementClient,
    SubscriptionError,
    SubscriptionStateStore,
    normalize_license_key,
    subscription_enforcement_required,
    subscription_origin,
)


LICENSE = "MSCR-ABCDEFGH-JKLMNPQR-STUVWXYZ-23456789"


class _Response:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int) -> bytes:
        return self.payload

    def getcode(self) -> int:
        return self.status


def test_license_format_and_origin_are_fail_closed(tmp_path):
    assert normalize_license_key(f"  {LICENSE.lower()} ") == LICENSE
    with pytest.raises(SubscriptionError):
        normalize_license_key("MSCR-short")

    valid = tmp_path / "valid.json"
    valid.write_text('{"origin":"https://sales.example.com"}', encoding="utf-8")
    assert subscription_origin(valid) == "https://sales.example.com"

    for origin in ("http://sales.example.com", "https://user@sales.example.com", "https://example.invalid"):
        path = tmp_path / f"invalid-{len(origin)}.json"
        path.write_text(json.dumps({"origin": origin}), encoding="utf-8")
        with pytest.raises(SubscriptionError):
            subscription_origin(path)


def test_subscription_build_profile_is_locked_by_default(tmp_path):
    missing = tmp_path / "missing.json"
    assert subscription_enforcement_required(missing) is True

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    assert subscription_enforcement_required(invalid) is True

    incomplete = tmp_path / "incomplete.json"
    incomplete.write_text(
        json.dumps({"subscription_required": False}),
        encoding="utf-8",
    )
    assert subscription_enforcement_required(incomplete) is True

    production = tmp_path / "production.json"
    production.write_text(
        json.dumps(
            {
                "subscription_required": True,
                "build_channel": "production",
            }
        ),
        encoding="utf-8",
    )
    assert subscription_enforcement_required(production) is True


def test_testing_unlocked_profile_requires_both_exact_markers(tmp_path):
    unlocked = tmp_path / "testing-unlocked.json"
    unlocked.write_text(
        json.dumps(
            {
                "subscription_required": False,
                "build_channel": "testing-unlocked",
            }
        ),
        encoding="utf-8",
    )
    assert subscription_enforcement_required(unlocked) is False

    wrong_type = tmp_path / "wrong-type.json"
    wrong_type.write_text(
        json.dumps(
            {
                "subscription_required": 0,
                "build_channel": "testing-unlocked",
            }
        ),
        encoding="utf-8",
    )
    assert subscription_enforcement_required(wrong_type) is True


def test_frozen_runtime_uses_build_profile_not_environment(monkeypatch):
    monkeypatch.setattr(app_main.sys, "frozen", True, raising=False)
    monkeypatch.setenv("MANUSCRIPT_TEST_BYPASS_SUBSCRIPTION", "1")
    monkeypatch.setattr(
        app_main,
        "subscription_enforcement_required",
        lambda: False,
    )
    assert app_main._subscription_required_for_runtime() is False

    monkeypatch.setattr(
        app_main,
        "subscription_enforcement_required",
        lambda: True,
    )
    assert app_main._subscription_required_for_runtime() is True


def test_source_runtime_keeps_explicit_environment_bypass(monkeypatch):
    monkeypatch.delattr(app_main.sys, "frozen", raising=False)
    monkeypatch.delenv("MANUSCRIPT_TEST_BYPASS_SUBSCRIPTION", raising=False)
    assert app_main._subscription_required_for_runtime() is True

    monkeypatch.setenv("MANUSCRIPT_TEST_BYPASS_SUBSCRIPTION", "1")
    assert app_main._subscription_required_for_runtime() is False


def test_production_runtime_rejects_hidden_test_commands(monkeypatch) -> None:
    """The probe gate follows the BUILD CHANNEL, not the subscription flag.

    Until 2026-09-24 this asserted allowed(args, subscription_required=False)
    is True - that switching subscription off also opened --workflow-probe.
    That held only while the testing package was the one build with
    subscription off. Manuscript is no longer subscription-gated, so the old
    assertion describes a hole: every shipped build would expose keystroke
    synthesis, the microphone and a spawned process.

    Both values of subscription_required are passed below precisely to show
    the gate no longer moves with it.
    """

    allowed = getattr(app_main, "_internal_test_command_allowed", None)
    assert callable(allowed)
    parser = app_main.build_parser()
    internal = (
        ["--smoke-test"],
        ["--workflow-probe"],
        ["--workflow-probe-target"],
        ["--internal-evaluation"],
    )

    monkeypatch.setattr(app_main, "is_testing_unlocked_build", lambda: False)
    for arguments in internal:
        args = parser.parse_args(arguments)
        assert allowed(args, subscription_required=True) is False
        assert allowed(args, subscription_required=False) is False

    monkeypatch.setattr(app_main, "is_testing_unlocked_build", lambda: True)
    for arguments in internal:
        args = parser.parse_args(arguments)
        assert allowed(args, subscription_required=True) is True

    ordinary = parser.parse_args([])
    monkeypatch.setattr(app_main, "is_testing_unlocked_build", lambda: False)
    assert allowed(ordinary, subscription_required=True) is True


def test_entitlement_client_uses_only_the_fixed_https_endpoint():
    captured = {}

    def open_request(request, *, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _Response({"active": True, "status": "active", "nextCheckSeconds": 900})

    client = EntitlementClient("https://sales.example.com", opener=open_request)
    result = client.validate(LICENSE, "a" * 64, timeout=4.0)

    assert result.active is True
    assert captured == {
        "url": "https://sales.example.com/api/license/validate",
        "timeout": 4.0,
        "body": {"licenseKey": LICENSE, "deviceHash": "a" * 64},
    }
    with pytest.raises(ValueError):
        EntitlementClient("http://sales.example.com")


def test_subscription_state_is_dpapi_protected_for_the_current_windows_user(tmp_path):
    store = SubscriptionStateStore(tmp_path / "manuscript-subscription.dpapi")
    state = store.save(LICENSE, device_secret="ab" * 32)

    raw = store.path.read_bytes()
    assert LICENSE.encode("utf-8") not in raw
    assert state.device_hash == __import__("hashlib").sha256(bytes.fromhex("ab" * 32)).hexdigest()
    assert store.load() == state

    store.clear()
    assert store.load() is None
