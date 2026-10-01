"""Shared rotation-aware evidence cursor for app startup and Word runs."""
import os
from pathlib import Path


def capture_cursor(log: Path) -> dict:
    """Capture a file identity and byte offset that survive log rotation."""
    with log.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        return {"file_id": os.fstat(stream.fileno()).st_ino, "offset": stream.tell()}


def lines_since(log: Path, since: int | dict = 0) -> list[str]:
    # Integer offsets remain supported for existing probes and saved fixtures.
    if isinstance(since, int):
        try:
            return log.read_text(encoding="utf-8", errors="replace").splitlines()[since:]
        except OSError:
            return []
    chunks = []
    found = False
    # Match the application's RotatingFileHandler backupCount=3. Losing the
    # cursor is an invalid measurement, never permission to read older events.
    for path in [log.with_name(log.name + f".{n}") for n in (3, 2, 1)] + [log]:
        try:
            with path.open("rb") as stream:
                stat = os.fstat(stream.fileno())
                if not found and stat.st_ino == since["file_id"]:
                    if stat.st_size < since["offset"]:
                        raise RuntimeError("application log truncated during measurement")
                    stream.seek(since["offset"])
                    found = True
                if found:
                    chunks.append(stream.read())
        except FileNotFoundError:
            continue
    if not found:
        raise RuntimeError("application log cursor lost during measurement")
    return b"".join(chunks).decode("utf-8", errors="replace").splitlines()

