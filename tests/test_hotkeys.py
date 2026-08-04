from __future__ import annotations

import ctypes
import threading
from types import SimpleNamespace

import pytest

import echo_link_core.hotkeys as hotkeys_module
from echo_link_core.hotkeys import hotkeys_overlap, normalize_hotkey_key, parse_hotkey


def test_hotkey_parser_normalizes_aliases():
    spec = parse_hotkey("Control + Windows")
    assert spec.canonical == "ctrl+win"
    assert len(spec.keys) == 2


def test_hotkey_parser_supports_function_keys():
    assert parse_hotkey("ctrl+F9").canonical == "ctrl+f9"


def test_single_escape_is_allowed_only_for_emergency_listener():
    with pytest.raises(ValueError):
        parse_hotkey("escape")
    assert parse_hotkey("escape", minimum_keys=1).canonical == "escape"


def test_push_to_talk_and_hands_free_hotkeys_cannot_overlap():
    assert hotkeys_overlap("ctrl+win", "ctrl+shift+win") is True
    assert hotkeys_overlap("ctrl+win", "ctrl+alt+space") is False


def test_registration_probe_reports_real_listener_state(monkeypatch):
    class FakeListener:
        registration_error = ""

        def __init__(self, _hotkey, _press, _release, _error):
            self.started = False

        def start(self):
            self.started = True

        def wait_until_ready(self, timeout):
            return self.started and timeout > 0

        def stop(self):
            self.started = False

        def join(self, timeout):
            assert timeout > 0

    monkeypatch.setattr(hotkeys_module, "GlobalPushToTalk", FakeListener)
    assert hotkeys_module.probe_hotkey_registration("ctrl+win") == {
        "registered": True,
        "error": "",
    }


def test_quick_tap_runs_press_then_release_without_stuck_recording(monkeypatch):
    order = []
    state = {"value": "ready"}
    press_started = threading.Event()
    allow_press_to_finish = threading.Event()
    release_finished = threading.Event()

    def on_press():
        press_started.set()
        assert allow_press_to_finish.wait(1.0)
        state["value"] = "listening"
        order.append("press")

    def on_release():
        order.append("release")
        if state["value"] == "listening":
            state["value"] = "ready"
        release_finished.set()

    listener = hotkeys_module.GlobalPushToTalk("ctrl+win", on_press, on_release)
    monkeypatch.setattr(
        hotkeys_module.ctypes,
        "windll",
        SimpleNamespace(user32=SimpleNamespace(CallNextHookEx=lambda *_args: 0)),
    )

    def fire(message, key):
        data = hotkeys_module._KeyboardHookStruct(key, 0, 0, 0, None)
        listener._hook_callback(0, message, ctypes.addressof(data))

    listener._start_callback_dispatcher()
    try:
        fire(listener.WM_KEYDOWN, hotkeys_module.VK_MAP["ctrl"])
        fire(listener.WM_KEYDOWN, hotkeys_module.VK_MAP["win"])
        assert press_started.wait(1.0)
        fire(listener.WM_KEYUP, hotkeys_module.VK_MAP["win"])
        allow_press_to_finish.set()
        assert release_finished.wait(1.0)
    finally:
        listener._stop_callback_dispatcher()

    assert order == ["press", "release"]
    assert state["value"] == "ready"


@pytest.mark.parametrize(
    ("physical", "normalized"),
    [
        (0xA2, 0x11),
        (0xA3, 0x11),
        (0xA0, 0x10),
        (0xA1, 0x10),
        (0xA4, 0x12),
        (0xA5, 0x12),
        (0x5B, 0x5B),
        (0x5C, 0x5B),
    ],
)
def test_hotkey_normalizes_left_and_right_modifiers(physical, normalized):
    assert normalize_hotkey_key(physical) == normalized


@pytest.mark.parametrize("value", ["", "ctrl", "ctrl+madeup"])
def test_hotkey_parser_rejects_unsafe_values(value):
    with pytest.raises(ValueError):
        parse_hotkey(value)
