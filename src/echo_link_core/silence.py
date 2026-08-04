from __future__ import annotations

import time


class SilenceAutoStopTracker:
    """Small local energy VAD used only to stop a hands-free recording."""

    def __init__(
        self,
        silence_seconds: float = 2.5,
        *,
        voice_threshold: float = 0.04,
        minimum_voice_seconds: float = 0.12,
    ):
        self.silence_seconds = max(0.25, float(silence_seconds))
        self.voice_threshold = max(0.001, float(voice_threshold))
        self.minimum_voice_seconds = max(0.0, float(minimum_voice_seconds))
        self._voice_candidate_at: float | None = None
        self._last_voice_at: float | None = None
        self._speech_seen = False

    @property
    def speech_seen(self) -> bool:
        return self._speech_seen

    def observe(self, level: float, now: float | None = None) -> None:
        timestamp = time.monotonic() if now is None else float(now)
        if float(level) < self.voice_threshold:
            self._voice_candidate_at = None
            return
        if self._speech_seen:
            self._last_voice_at = timestamp
            return
        if self._voice_candidate_at is None:
            self._voice_candidate_at = timestamp
        if timestamp - self._voice_candidate_at >= self.minimum_voice_seconds:
            self._speech_seen = True
            self._last_voice_at = timestamp

    def should_stop(self, now: float | None = None) -> bool:
        if not self._speech_seen or self._last_voice_at is None:
            return False
        timestamp = time.monotonic() if now is None else float(now)
        return timestamp - self._last_voice_at >= self.silence_seconds
