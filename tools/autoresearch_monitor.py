"""A small on-screen panel showing that AutoResearch is running.

    .venv/Scripts/pythonw.exe tools/autoresearch_monitor.py

Curtis asked to be able to see the loop working, and for it to appear only while
it is working. The runner starts this, and it closes itself the moment the
status file goes away - so "is it running?" is answered by looking at the
screen, not by reading a log.

IT MUST NEVER TAKE FOCUS. That is not a nicety, it is the single constraint that
decides whether this feature is safe to build at all. The HUD follows editable
focus by design, and on 2026-08-28 a window stealing focus mid-experiment turned
a corrected essay into "the review on screen was captured from 'Claude'" and
cost several measurements. A monitor that crashed the runs it was monitoring
would be worse than no monitor.

Three separate mechanisms enforce that, because one is not enough on Windows:

    WindowDoesNotAcceptFocus   the window manager never gives it keyboard focus
    WA_ShowWithoutActivating   showing it does not activate it
    WindowTransparentForInput  clicks pass straight through to whatever is under

The last one also means it cannot be moved or closed by hand, which is correct:
it is a readout, not a control, and it disappears on its own.

Read-only. It polls one JSON file and paints it.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import autoresearch_status  # noqa: E402

# Slow on purpose. Nothing here is urgent, and a tight repaint loop on a machine
# that is driving Word is a cost with no benefit.
_POLL_MILLISECONDS = 1000

# How long the file may be missing before this closes. A brief gap is normal -
# the runner replaces the file atomically between phases - and closing on the
# first miss would make the panel flicker out mid-run.
_MISSES_BEFORE_CLOSING = 4


def main() -> int:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

    application = QApplication(sys.argv)

    window = QWidget()
    window.setWindowTitle("AutoResearch")
    window.setWindowFlags(
        Qt.FramelessWindowHint
        | Qt.WindowStaysOnTopHint
        | Qt.Tool  # keeps it off the taskbar and out of alt-tab
        | Qt.WindowDoesNotAcceptFocus
        | Qt.WindowTransparentForInput
    )
    window.setAttribute(Qt.WA_ShowWithoutActivating, True)
    window.setAttribute(Qt.WA_TranslucentBackground, False)
    window.setStyleSheet(
        "QWidget { background: #101418; border: 1px solid #2ecc71; }"
        "QLabel { color: #d7e0e6; }"
    )

    layout = QVBoxLayout(window)
    layout.setContentsMargins(14, 10, 14, 10)
    layout.setSpacing(3)

    heading = QLabel("AUTORESEARCH RUNNING")
    heading_font = QFont()
    heading_font.setBold(True)
    heading_font.setPointSize(10)
    heading.setFont(heading_font)
    heading.setStyleSheet("color: #2ecc71; border: none;")
    layout.addWidget(heading)

    body = QLabel("starting")
    body_font = QFont("Consolas")
    body_font.setPointSize(9)
    body.setFont(body_font)
    body.setStyleSheet("border: none;")
    layout.addWidget(body)

    state = {"misses": 0}

    def _is_stale(status: dict) -> bool:
        """Has the writer stopped updating this file?

        A run killed part way cannot clear its own status file, so the panel
        went on announcing "reliability hard baseline" for hours after the run
        died. That is worse than showing nothing: an empty screen says nothing,
        and a stale panel claims work is happening.

        Curtis saw exactly this on 2026-09-04.
        """
        from datetime import datetime

        try:
            written = datetime.fromisoformat(str(status.get("stamp", "")))
        except ValueError:
            return False
        age = (datetime.now() - written).total_seconds()
        return age > autoresearch_status.STALE_AFTER_SECONDS

    def refresh() -> None:
        status = autoresearch_status.read_status()
        if status is not None and _is_stale(status):
            status = None
        if status is None:
            state["misses"] += 1
            if state["misses"] >= _MISSES_BEFORE_CLOSING:
                application.quit()
            return
        state["misses"] = 0

        lines = [
            f"experiment {status.get('experiment', '?')}   "
            f"{status.get('phase', '')}",
            f"trying     {str(status.get('description', ''))[:46]}",
            f"essays     {status.get('essays', '')}",
            f"baseline   {status.get('baseline', '-')}"
            f"    best {status.get('best', '-')}",
            f"last       {status.get('last_result', '-')}",
            f"kept {status.get('kept', 0)}   discarded {status.get('discarded', 0)}"
            f"   crashed {status.get('crashed', 0)}",
        ]
        body.setText("\n".join(lines))
        window.adjustSize()

    timer = QTimer()
    timer.timeout.connect(refresh)
    timer.start(_POLL_MILLISECONDS)
    refresh()

    # Top right, clear of the HUD, which sits near the caret in the document.
    screen = application.primaryScreen().availableGeometry()
    window.adjustSize()
    window.move(screen.right() - window.width() - 24, screen.top() + 24)
    window.show()

    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
