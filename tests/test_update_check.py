from __future__ import annotations

import json

import pytest

from echo_link_core.update_check import (
    UpdateCheckError,
    UpdateClient,
    check_now,
)


ORIGIN = "https://updates.example.com"


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


def _opener(payload: dict[str, object]):
    def opener(_request, timeout=10.0):
        return _Response(payload)

    return opener


VALID = {
    "version": "0.2.0",
    "downloadUrl": f"{ORIGIN}/releases/ManuscriptV2-0.2.0.exe",
    "sha256": "a" * 64,
    "notes": "Fixes.",
}


def test_a_newer_version_is_reported_available():
    client = UpdateClient(ORIGIN, opener=_opener(VALID))
    info = client.check(current_version="0.1.0")
    assert info.available is True
    assert info.version == "0.2.0"
    assert info.download_url == VALID["downloadUrl"]
    assert info.sha256 == "a" * 64


def test_an_older_or_equal_version_is_not_reported_available():
    for candidate in ("0.1.0", "0.0.9"):
        client = UpdateClient(ORIGIN, opener=_opener({**VALID, "version": candidate}))
        assert client.check(current_version="0.1.0").available is False


def test_a_downgrade_is_refused_not_just_ignored():
    # Same assertion as the equal/older case, but this is the one an
    # off-by-one direction bug would get backwards: a server offering an
    # OLDER build than what is installed must never be treated as an update.
    client = UpdateClient(ORIGIN, opener=_opener({**VALID, "version": "0.0.1"}))
    info = client.check(current_version="0.5.0")
    assert info.available is False


def test_a_tampered_manifest_missing_a_checksum_is_refused():
    payload = dict(VALID)
    del payload["sha256"]
    client = UpdateClient(ORIGIN, opener=_opener(payload))
    with pytest.raises(UpdateCheckError):
        client.check(current_version="0.1.0")


def test_a_download_url_on_a_different_host_is_refused():
    payload = {**VALID, "downloadUrl": "https://attacker.example.net/payload.exe"}
    client = UpdateClient(ORIGIN, opener=_opener(payload))
    with pytest.raises(UpdateCheckError):
        client.check(current_version="0.1.0")


def test_a_non_https_origin_is_refused_at_construction():
    with pytest.raises(UpdateCheckError):
        UpdateClient("http://updates.example.com")


def test_an_invalid_installed_version_is_refused_rather_than_guessed():
    client = UpdateClient(ORIGIN, opener=_opener(VALID))
    with pytest.raises(UpdateCheckError):
        client.check(current_version="not-a-version")


def test_a_network_failure_fails_quietly_as_an_error_not_a_crash():
    def opener(_request, timeout=10.0):
        raise OSError("no network")

    client = UpdateClient(ORIGIN, opener=opener)
    with pytest.raises(UpdateCheckError):
        client.check(current_version="0.1.0")


def test_check_now_is_the_only_public_entry_point_and_takes_no_schedule():
    # This module must never schedule itself. The only way anything happens
    # is a caller invoking check_now (or UpdateClient.check) directly.
    import echo_link_core.update_check as module

    assert not any(
        name in dir(module)
        for name in ("schedule", "start_periodic_check", "install_timer")
    )
    assert callable(check_now)
