import pytest

from echo_link_core.update_check import UpdateCheckError, _validate_download_url


@pytest.mark.parametrize("url", [
    "https://updates.example.com:444/setup.exe",
    "https://updates.example.com:bad/setup.exe",
    "https://updates.example.com/setup.exe#other",
])
def test_update_download_rejects_changed_port_or_fragment(url):
    with pytest.raises(UpdateCheckError):
        _validate_download_url(url, origin="https://updates.example.com")


def test_update_download_accepts_equivalent_default_https_port():
    url = "https://updates.example.com:443/setup.exe"
    assert _validate_download_url(url, origin="https://updates.example.com") == url


def test_update_download_accepts_configured_nondefault_port_only():
    origin = "https://updates.example.com:8443"
    url = origin + "/setup.exe"
    assert _validate_download_url(url, origin=origin) == url
    with pytest.raises(UpdateCheckError):
        _validate_download_url("https://updates.example.com/setup.exe", origin=origin)
