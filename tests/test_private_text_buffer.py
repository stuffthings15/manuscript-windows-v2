from __future__ import annotations

from pathlib import Path

from echo_link_core.private_text_buffer import PrivateTextBuffer


def test_private_text_buffer_stages_overwrites_and_clears() -> None:
    buffer = PrivateTextBuffer()

    assert buffer.peek() is None
    assert buffer.status().available is False

    first = buffer.stage("first private value")
    assert first.available is True
    assert first.character_count == 19
    assert buffer.peek() == "first private value"

    second = buffer.stage("replacement")
    assert second.generation == first.generation + 1
    assert buffer.peek() == "replacement"

    cleared = buffer.clear()
    assert cleared.available is False
    assert cleared.character_count == 0
    assert buffer.peek() is None


def test_private_text_buffer_rejects_empty_and_null_text() -> None:
    buffer = PrivateTextBuffer()

    try:
        buffer.stage("")
    except ValueError as exc:
        assert "empty" in str(exc)
    else:
        raise AssertionError("Empty private paste text was accepted.")

    try:
        buffer.stage("left\x00right")
    except ValueError as exc:
        assert "null" in str(exc)
    else:
        raise AssertionError("Null-containing private paste text was accepted.")


def test_echo_link_stage_actions_do_not_use_the_windows_clipboard() -> None:
    window_source = (
        Path(__file__).resolve().parents[1] / "src" / "echo_link_app" / "window.py"
    ).read_text(encoding="utf-8")

    assert "import pyperclip" not in window_source
    assert "pyperclip.copy" not in window_source
