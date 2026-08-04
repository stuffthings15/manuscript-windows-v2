from __future__ import annotations

import os

import pytest
from PySide6.QtWidgets import QApplication

from echo_link_app.theme import apply_dark_theme
from echo_link_app.window import EchoLinkWindow
from echo_link_core.models import SessionEvent, SessionState
from echo_link_core.storage import EchoLinkStore


@pytest.fixture(scope="module")
def app():
    os.environ["ECHOLINK_DISABLE_SOUNDS"] = "1"
    os.environ["ECHOLINK_DISABLE_MEDIA_CONTROL"] = "1"
    application = QApplication.instance() or QApplication([])
    application.setStyle("Fusion")
    apply_dark_theme(application)
    return application


def test_transcription_error_returns_record_button_to_ready_state(app, tmp_path):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)

    window._handle_session_event(SessionEvent(SessionState.LISTENING, "Listening..."))
    window._handle_session_event(SessionEvent(SessionState.TRANSCRIBING, "Processing locally..."))
    assert window.record_button.isEnabled() is False

    window._handle_session_event(
        SessionEvent(SessionState.ERROR, "No speech was detected. Check the microphone and try again.")
    )

    assert window.record_button.text() == "Start recording"
    assert window.record_button.isEnabled() is True
    assert window.retry_button.isEnabled() is True
    window.close()
