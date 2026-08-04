from __future__ import annotations

import asyncio
import logging
import queue
import sys
import threading
from collections.abc import Callable


logger = logging.getLogger(__name__)


class MediaPauseController:
    """Serializes Windows media pause/resume and resumes only owned sessions."""

    def __init__(self, manager_request: Callable | None = None, *, enabled: bool = True):
        self._manager_request = manager_request or _default_manager_request
        self._enabled = bool(enabled)
        self._commands: queue.Queue[str] = queue.Queue()
        self._paused_sessions: list[object] = []
        self._thread: threading.Thread | None = None
        if self._enabled:
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name="echo-link-media-control",
            )
            self._thread.start()

    def pause(self) -> None:
        if self._enabled:
            self._commands.put("pause")

    def resume(self) -> None:
        if self._enabled:
            self._commands.put("resume")

    def shutdown(self, timeout: float = 2.0) -> None:
        if self._thread is None:
            return
        self._commands.put("resume")
        self._commands.put("stop")
        self._thread.join(timeout=max(0.0, float(timeout)))

    @property
    def paused_count(self) -> int:
        return len(self._paused_sessions)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            while True:
                command = self._commands.get()
                if command == "stop":
                    return
                try:
                    if command == "pause":
                        loop.run_until_complete(self._pause_playing())
                    elif command == "resume":
                        loop.run_until_complete(self._resume_owned())
                except Exception:
                    logger.warning("Windows media %s failed", command, exc_info=True)
        finally:
            loop.close()

    async def _pause_playing(self) -> None:
        if self._paused_sessions:
            return
        manager = await self._manager_request()
        paused: list[object] = []
        for session in manager.get_sessions():
            try:
                if _status_name(session) != "playing":
                    continue
                if await session.try_pause_async():
                    paused.append(session)
            except Exception:
                logger.debug("Media session could not be paused", exc_info=True)
        self._paused_sessions = paused
        if paused:
            logger.info("Paused %d active media session(s) for dictation", len(paused))

    async def _resume_owned(self) -> None:
        sessions, self._paused_sessions = self._paused_sessions, []
        resumed = 0
        for session in sessions:
            try:
                if _status_name(session) != "paused":
                    continue
                if await session.try_play_async():
                    resumed += 1
            except Exception:
                logger.debug("Media session could not be resumed", exc_info=True)
        if resumed:
            logger.info("Resumed %d media session(s) paused by Manuscript", resumed)


async def _default_manager_request():
    if sys.platform != "win32":
        raise RuntimeError("Windows media control is unavailable on this platform.")
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager

    return await GlobalSystemMediaTransportControlsSessionManager.request_async()


def _status_name(session) -> str:
    status = session.get_playback_info().playback_status
    name = getattr(status, "name", None)
    if name:
        return str(name).casefold()
    return str(status).rsplit(".", 1)[-1].casefold()
