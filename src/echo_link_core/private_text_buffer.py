from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PrivateTextStatus:
    available: bool
    character_count: int
    generation: int
    staged_at: float | None


class PrivateTextBuffer:
    """Process-memory-only text for Manuscript's separate copy/paste path."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._text: str | None = None
        self._generation = 0
        self._staged_at: float | None = None

    def stage(self, text: str) -> PrivateTextStatus:
        value = str(text)
        if not value:
            raise ValueError("Private paste text is empty.")
        if "\x00" in value:
            raise ValueError("Private paste text cannot contain a null character.")
        with self._lock:
            self._text = value
            self._generation += 1
            self._staged_at = time.monotonic()
            return self._status_locked()

    def peek(self) -> str | None:
        with self._lock:
            return self._text

    def clear(self) -> PrivateTextStatus:
        with self._lock:
            self._text = None
            self._generation += 1
            self._staged_at = None
            return self._status_locked()

    def status(self) -> PrivateTextStatus:
        with self._lock:
            return self._status_locked()

    def _status_locked(self) -> PrivateTextStatus:
        return PrivateTextStatus(
            available=self._text is not None,
            character_count=len(self._text or ""),
            generation=self._generation,
            staged_at=self._staged_at,
        )
