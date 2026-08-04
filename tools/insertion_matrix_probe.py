from __future__ import annotations

import ctypes
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PySide6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit, QTextEdit, QWidget

from echo_link_core.insertion import WindowsTextInserter, active_window


def _pump(app: QApplication, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


def _value(widget) -> str:
    if isinstance(widget, QLineEdit):
        return widget.text()
    return widget.toPlainText()


def main() -> int:
    if sys.platform != "win32":
        print("Insertion matrix requires Windows.")
        return 1
    app = QApplication.instance() or QApplication([])
    user32 = ctypes.windll.user32
    results: list[tuple[str, bool, str]] = []
    for name, target in (
        ("single-line", QLineEdit()),
        ("plain-text", QPlainTextEdit()),
        ("rich-text", QTextEdit()),
    ):
        target.setWindowTitle(f"Manuscript matrix {name}")
        target.resize(460, 120)
        target.show()
        target.setFocus()
        user32.SetForegroundWindow(int(target.winId()))
        _pump(app, 0.2)
        snapshot = active_window()
        review = QWidget()
        review.setWindowTitle("Manuscript review surface")
        review.resize(300, 90)
        review.show()
        user32.SetForegroundWindow(int(review.winId()))
        _pump(app, 0.15)
        expected = f"Manuscript {name} insertion"
        record = WindowsTextInserter().insert_into(snapshot, expected)
        _pump(app, 0.35)
        actual = _value(target)
        passed = record is not None and actual == expected
        results.append((name, passed, actual))
        target.close()
        review.close()
        _pump(app, 0.05)
    for name, passed, actual in results:
        print(f"{name}: passed={passed} text={actual!r}")
    return 0 if all(passed for _, passed, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
