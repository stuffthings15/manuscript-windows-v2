from __future__ import annotations

import time
from types import SimpleNamespace

from echo_link_core.media_control import MediaPauseController


class FakeSession:
    def __init__(self, status: str):
        self.status = status
        self.pause_calls = 0
        self.play_calls = 0

    def get_playback_info(self):
        return SimpleNamespace(playback_status=SimpleNamespace(name=self.status.upper()))

    async def try_pause_async(self):
        self.pause_calls += 1
        self.status = "paused"
        return True

    async def try_play_async(self):
        self.play_calls += 1
        self.status = "playing"
        return True


class FakeManager:
    def __init__(self, sessions):
        self.sessions = sessions

    def get_sessions(self):
        return self.sessions


def _wait_for(predicate, timeout: float = 1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


def test_pauses_only_playing_media_and_resumes_owned_session():
    playing = FakeSession("playing")
    stopped = FakeSession("stopped")

    async def request():
        return FakeManager([playing, stopped])

    controller = MediaPauseController(request)
    controller.pause()
    _wait_for(lambda: playing.pause_calls == 1)
    assert stopped.pause_calls == 0
    controller.resume()
    _wait_for(lambda: playing.play_calls == 1)
    assert stopped.play_calls == 0
    controller.shutdown()


def test_does_not_resume_session_user_already_changed():
    playing = FakeSession("playing")

    async def request():
        return FakeManager([playing])

    controller = MediaPauseController(request)
    controller.pause()
    _wait_for(lambda: playing.pause_calls == 1)
    playing.status = "playing"
    controller.resume()
    _wait_for(lambda: controller.paused_count == 0)
    assert playing.play_calls == 0
    controller.shutdown()


def test_disabled_media_controller_is_noop():
    controller = MediaPauseController(enabled=False)
    controller.pause()
    controller.resume()
    controller.shutdown()
    assert controller.paused_count == 0
