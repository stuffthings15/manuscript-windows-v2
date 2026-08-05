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


def test_startup_registers_v2_without_removing_v1(monkeypatch) -> None:
    values = {
        "EchoLink": '"C:\\Old\\EchoLink.exe"',
        "Manuscript": '"C:\\Old\\Manuscript.exe"',
    }
    fake = _FakeWinReg(values)
    monkeypatch.setattr(startup.os, "name", "nt")
    monkeypatch.setattr(startup, "startup_command", lambda: '"C:\\New\\Manuscript.exe"')
    monkeypatch.setitem(sys.modules, "winreg", fake)

    startup.configure_startup(True)

    assert values == {
        "EchoLink": '"C:\\Old\\EchoLink.exe"',
        "Manuscript": '"C:\\Old\\Manuscript.exe"',
        "ManuscriptWindowsV2": '"C:\\New\\Manuscript.exe"',
    }


def test_disabling_startup_removes_only_v2_name(monkeypatch) -> None:
    values = {
        "EchoLink": "old",
        "Manuscript": "current",
        "ManuscriptWindowsV2": "v2",
    }
    fake = _FakeWinReg(values)
    monkeypatch.setattr(startup.os, "name", "nt")
    monkeypatch.setitem(sys.modules, "winreg", fake)

    startup.configure_startup(False)

    assert values == {"EchoLink": "old", "Manuscript": "current"}


def test_frozen_startup_command_opens_quietly_in_tray(monkeypatch) -> None:
    monkeypatch.setattr(startup.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        startup.sys,
        "executable",
        r"C:\Program Files\Manuscript Windows Version 2\ManuscriptV2.exe",
    )

    assert startup.startup_command() == (
        '"C:\\Program Files\\Manuscript Windows Version 2\\ManuscriptV2.exe" --startup'
    )


def test_source_startup_command_uses_canonical_v2_launcher(monkeypatch) -> None:
    monkeypatch.delattr(startup.sys, "frozen", raising=False)
    monkeypatch.setattr(startup.sys, "executable", r"C:\Python\python.exe")

    command = startup.startup_command()

    assert command.startswith('"C:\\Python\\python.exe" "')
    assert command.endswith('run_manuscript.py" --startup')


def test_startup_argument_is_supported() -> None:
    assert build_parser().parse_args(["--startup"]).startup is True


def test_normal_and_startup_launches_enable_global_hotkeys_by_default() -> None:
    parser = build_parser()

    assert parser.parse_args([]).enable_global_hotkeys is True
    assert parser.parse_args(["--startup"]).enable_global_hotkeys is True


def test_internal_launch_can_explicitly_disable_global_hotkeys() -> None:
    assert (
        build_parser().parse_args(["--disable-global-hotkeys"]).enable_global_hotkeys
        is False
    )
