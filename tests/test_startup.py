from __future__ import annotations

import sys

from echo_link_app import startup
from echo_link_app.main import build_parser


class _FakeKey:
    def __init__(self, values: dict[str, str]):
        self.values = values

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _FakeWinReg:
    HKEY_CURRENT_USER = object()
    REG_SZ = 1

    def __init__(self, values: dict[str, str]):
        self.values = values

    def CreateKey(self, _root, _path):
        return _FakeKey(self.values)

    def SetValueEx(self, key, name, _reserved, _kind, value):
        key.values[name] = value

    def DeleteValue(self, key, name):
        if name not in key.values:
            raise FileNotFoundError(name)
        del key.values[name]


def test_startup_rebrand_moves_legacy_registration(monkeypatch) -> None:
    values = {"EchoLink": '"C:\\Old\\EchoLink.exe"'}
    fake = _FakeWinReg(values)
    monkeypatch.setattr(startup.os, "name", "nt")
    monkeypatch.setattr(startup, "startup_command", lambda: '"C:\\New\\Manuscript.exe"')
    monkeypatch.setitem(sys.modules, "winreg", fake)

    startup.configure_startup(True)

    assert values == {"Manuscript": '"C:\\New\\Manuscript.exe"'}


def test_disabling_startup_removes_current_and_legacy_names(monkeypatch) -> None:
    values = {"EchoLink": "old", "Manuscript": "new"}
    fake = _FakeWinReg(values)
    monkeypatch.setattr(startup.os, "name", "nt")
    monkeypatch.setitem(sys.modules, "winreg", fake)

    startup.configure_startup(False)

    assert values == {}


def test_frozen_startup_command_opens_quietly_in_tray(monkeypatch) -> None:
    monkeypatch.setattr(startup.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        startup.sys,
        "executable",
        r"C:\Program Files\Manuscript\Manuscript.exe",
    )

    assert startup.startup_command() == (
        '"C:\\Program Files\\Manuscript\\Manuscript.exe" --startup'
    )


def test_source_startup_command_opens_quietly_in_tray(monkeypatch) -> None:
    monkeypatch.delattr(startup.sys, "frozen", raising=False)
    monkeypatch.setattr(startup.sys, "executable", r"C:\Python\python.exe")

    command = startup.startup_command()

    assert command.startswith('"C:\\Python\\python.exe" "')
    assert command.endswith('run_echo_link.py" --startup')


def test_startup_argument_is_supported() -> None:
    assert build_parser().parse_args(["--startup"]).startup is True
