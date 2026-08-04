from __future__ import annotations

import ctypes
import sys
import threading
import time
from pathlib import Path

import pyperclip
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit, QTextEdit, QWidget


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from echo_link_core.insertion import WindowsTextInserter


SOURCE = "Alpha beta gamma delta"
SELECTED = "beta gamma"
REPLACEMENT = "BETA GAMMA"
EXPECTED = "Alpha BETA GAMMA delta"


def pump(app: QApplication, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


def run_with_events(app: QApplication, action):
    result = {}

    def worker() -> None:
        try:
            result["value"] = action()
        except BaseException as exc:
            result["error"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    while thread.is_alive():
        app.processEvents()
        time.sleep(0.01)
    if "error" in result:
        raise result["error"]
    return result.get("value")


def set_text_and_selection(widget) -> None:
    if isinstance(widget, QLineEdit):
        widget.setText(SOURCE)
        widget.setSelection(SOURCE.index(SELECTED), len(SELECTED))
        return
    widget.setPlainText(SOURCE)
    cursor = widget.textCursor()
    cursor.setPosition(SOURCE.index(SELECTED))
    cursor.setPosition(SOURCE.index(SELECTED) + len(SELECTED), QTextCursor.MoveMode.KeepAnchor)
    widget.setTextCursor(cursor)


def value(widget) -> str:
    return widget.text() if isinstance(widget, QLineEdit) else widget.toPlainText()


def main() -> int:
    if sys.platform != "win32":
        print("Selected-text matrix requires Windows.")
        return 2
    app = QApplication.instance() or QApplication([])
    user32 = ctypes.windll.user32
    inserter = WindowsTextInserter()
    results = []
    for name, widget in (
        ("single-line", QLineEdit()),
        ("plain-text", QPlainTextEdit()),
        ("rich-text", QTextEdit()),
    ):
        sentinel = f"ECHOLINK_{name}_CLIPBOARD"
        widget.setWindowTitle(f"Manuscript selected-text {name}")
        widget.resize(520, 140)
        widget.show()
        widget.setFocus()
        widget.raise_()
        widget.activateWindow()
        user32.SetForegroundWindow(int(widget.winId()))
        pump(app, 0.2)
        pyperclip.copy(sentinel)
        set_text_and_selection(widget)
        pump(app, 0.05)
        captured = run_with_events(app, inserter.capture_selection)
        review = QWidget()
        review.setWindowTitle("Manuscript selected-text review")
        review.resize(320, 100)
        review.show()
        user32.SetForegroundWindow(int(review.winId()))
        pump(app, 0.15)
        replaced = run_with_events(app, lambda: inserter.replace_selection(captured, REPLACEMENT))
        pump(app, 0.35)
        clipboard_safe = pyperclip.paste() == sentinel
        passed = captured.text == SELECTED and replaced is not None and value(widget) == EXPECTED and clipboard_safe
        results.append((name, passed, captured.text, value(widget), clipboard_safe))
        widget.close()
        review.close()
        pump(app, 0.05)

    for name, passed, captured, actual, clipboard_safe in results:
        print(
            f"{name}: passed={passed} captured={captured!r} "
            f"text={actual!r} clipboard_restored={clipboard_safe}"
        )
    return 0 if all(item[1] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
