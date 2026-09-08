"""The app has two capture paths and only one of them named the document.

Measured 2026-09-08, one 20 essay batch, no exceptions either way:

    7 essays where the automatic focus monitor captured first -> all complete
    5 essays where the writer's shortcut captured first        -> all scored
                                                                  "the app
                                                                  reported no
                                                                  capture at
                                                                  all"

The app was right every time. Essay 10 built a review of 22 sections at 9005
characters, which is essay 10, and then waited five minutes while the harness
watched a log line that path never writes. Eight of twenty documents came back
as a crash out of a working product.

The two paths race. The monitor needs about sixteen seconds to settle after a
document opens and the batch presses the shortcut at fourteen to eighteen, so
which one wins is a coin flip that has nothing to do with whether the app
works. A harness must not be able to lose that flip, which means every capture
path names its document.
"""
from __future__ import annotations

import importlib
import logging
import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _entry in (ROOT / "tools", ROOT / "src"):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))


import pytest
from PySide6.QtWidgets import QApplication

from echo_link_app.theme import apply_dark_theme
from echo_link_app.window import EchoLinkWindow
from echo_link_core.insertion import SelectionRecord, WindowSnapshot
from echo_link_core.storage import EchoLinkStore
from echo_link_core.windows_selection import DetectedSelection

# The title the batch actually saw, digest prefix and Compatibility Mode and
# all, because the harness matches a substring of it.
ESSAY_10 = (
    "462dbd9d3995-10_the_return_of_passenger_rail  -  Compatibility Mode - Word"
)


@pytest.fixture(scope="module")
def app():
    os.environ["ECHOLINK_DISABLE_SOUNDS"] = "1"
    os.environ["ECHOLINK_DISABLE_MEDIA_CONTROL"] = "1"
    application = QApplication.instance() or QApplication([])
    application.setStyle("Fusion")
    apply_dark_theme(application)
    return application


class _ShortcutReader:
    """A reader that answers the shortcut, the way Word does for a bare caret."""

    def __init__(self, record):
        self._record = record

    def read(self):
        return DetectedSelection(self._record, (100, 120, 230, 145))

    def read_document(self):
        return DetectedSelection(self._record, (100, 120, 230, 145))


def test_the_shortcut_capture_names_the_document_it_captured(
    app, tmp_path, monkeypatch, caplog
):
    """Without this the app can say it built a review and never say from what."""
    record = SelectionRecord(
        "The line opened in 1974. It carrys four million passengers.",
        WindowSnapshot(77, 88, ESSAY_10),
        scope="document",
    )
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    monkeypatch.setattr(
        "echo_link_app.window.WindowsSelectionReader",
        lambda **_kwargs: _ShortcutReader(record),
    )

    with caplog.at_level(logging.INFO, logger="echo_link_app.window"):
        window._capture_selected_text_for_palette()
        app.processEvents()

    assert window.selected_text_record is not None
    named = [
        message
        for message in caplog.messages
        if ESSAY_10 in message and "capture" in message
    ]
    assert named, (
        "The shortcut captured a document and logged no line naming it. The "
        "app's log has to say WHICH document a review was built from, on every "
        "capture path, or an observer cannot tell a correct capture from a "
        "missing one - which cost 8 of 20 essays on 2026-09-08."
    )

    window.rewrite_palette.finish_choice()
    window._quitting = True
    window.close()


def test_the_batch_reads_a_shortcut_capture_as_a_capture(monkeypatch):
    """The harness side of the same defect, against the real log lines.

    Both strings below are copied from echo-link.log for essay 12 and essay 10
    of the 2026-09-08 run: the one the harness accepted and the one it could
    not see. `_lines_since` is the only thing stubbed, so the pattern under
    test is the batch's own and not a copy of it - a copy passes with the fix
    removed, which is what makes it decoration.
    """
    sys.modules.setdefault("uiautomation", types.ModuleType("uiautomation"))
    batch = importlib.import_module("run_essays_batch")

    monitor_line = (
        "2026-09-08 01:33:47,102 INFO echo_link_core.windows_selection: Writing "
        "HUD editable focus detected title='af215c9c6a05-12_food_waste_across_"
        "the_supply_chain  -  Compatibility Mode - Word' scope=document "
        "chars=8948 anchor=(1180, 761, 1181, 791)"
    )
    shortcut_line = (
        "2026-09-08 01:23:19,004 INFO echo_link_app.window: Writing HUD "
        f"shortcut capture prepared title='{ESSAY_10}' scope=document chars=9005"
    )

    monkeypatch.setattr(batch, "_lines_since", lambda _since: [monitor_line])
    assert "12_food_waste_across_the_supply_chain" in batch.captured_document_title(0)

    monkeypatch.setattr(batch, "_lines_since", lambda _since: [shortcut_line])
    assert batch.captured_document_title(0) == ESSAY_10, (
        "The batch read the app's log and could not see a capture the app "
        "really made. That is what scored 8 of 20 working essays a crash."
    )


def test_the_wait_stops_on_a_shortcut_capture(monkeypatch):
    """The parser is not the point; the five minute wait is.

    wait_for_document_capture is what actually burned the 300 seconds, so it
    is what has to return.
    """
    sys.modules.setdefault("uiautomation", types.ModuleType("uiautomation"))
    batch = importlib.import_module("run_essays_batch")

    shortcut_line = (
        "2026-09-08 01:23:19,004 INFO echo_link_app.window: Writing HUD "
        f"shortcut capture prepared title='{ESSAY_10}' scope=document chars=9005"
    )
    monkeypatch.setattr(batch, "_lines_since", lambda _since: [shortcut_line])
    monkeypatch.setattr(batch, "_log_position", lambda: 1)
    monkeypatch.setattr(batch, "app_is_running", lambda: True)

    captured = batch.wait_for_document_capture(
        "10_the_return_of_passenger_rail", since=0, timeout=5.0, quiet=1.0
    )

    assert captured == ESSAY_10
