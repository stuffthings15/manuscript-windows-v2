from __future__ import annotations

import pytest

from echo_link_core.private_clipboard import (
    CF_UNICODETEXT,
    EXCLUDE_MONITOR_FORMAT,
    INCLUDE_HISTORY_FORMAT,
    UPLOAD_CLOUD_FORMAT,
    copy_text_private,
)


class FakeClipboardAPI:
    def __init__(self, *, fail_format: int | None = None) -> None:
        self.formats = {
            EXCLUDE_MONITOR_FORMAT: 1001,
            INCLUDE_HISTORY_FORMAT: 1002,
            UPLOAD_CLOUD_FORMAT: 1003,
        }
        self.payloads: dict[int, bytes] = {}
        self.transferred: list[tuple[int, int]] = []
        self.freed: list[int] = []
        self.opened = False
        self.closed = False
        self.emptied = False
        self.fail_format = fail_format
        self._next_handle = 2000

    def register_format(self, name: str) -> int:
        return self.formats[name]

    def allocate(self, payload: bytes) -> int:
        self._next_handle += 1
        self.payloads[self._next_handle] = payload
        return self._next_handle

    def open(self) -> bool:
        self.opened = True
        return True

    def empty(self) -> bool:
        self.emptied = True
        return True

    def set_data(self, format_id: int, handle: int) -> bool:
        if format_id == self.fail_format:
            return False
        self.transferred.append((format_id, handle))
        return True

    def close(self) -> None:
        self.closed = True

    def free(self, handle: int) -> None:
        self.freed.append(handle)

    def error(self, message: str) -> OSError:
        return OSError(message)


def test_private_clipboard_sets_all_exclusion_formats_before_text() -> None:
    api = FakeClipboardAPI()

    receipt = copy_text_private("private text", _api=api)

    assert receipt.history_excluded is True
    assert receipt.cloud_upload_excluded is True
    assert receipt.monitor_processing_excluded is True
    assert [format_id for format_id, _handle in api.transferred] == [
        1001,
        1002,
        1003,
        CF_UNICODETEXT,
    ]
    assert api.payloads[api.transferred[-1][1]].decode("utf-16-le").rstrip("\x00") == "private text"
    assert api.emptied is True
    assert api.closed is True
    assert api.freed == []


def test_private_clipboard_fails_before_transferring_text_when_marker_fails() -> None:
    api = FakeClipboardAPI(fail_format=1002)

    with pytest.raises(OSError, match="rejected private clipboard"):
        copy_text_private("must not leak", _api=api)

    assert CF_UNICODETEXT not in [format_id for format_id, _handle in api.transferred]
    assert api.closed is True
    assert len(api.freed) == 3


def test_private_clipboard_rejects_embedded_null() -> None:
    with pytest.raises(ValueError, match="null character"):
        copy_text_private("left\x00right", _api=FakeClipboardAPI())
