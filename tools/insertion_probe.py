from __future__ import annotations

import ctypes
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PySide6.QtWidgets import QApplication, QLineEdit, QWidget

from echo_link_core.insertion import WindowsTextInserter, active_window


def _pump(app: QApplication, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


def main() -> int:
    if sys.platform != "win32":
        print("Insertion probe requires Windows.")
        return 1

    app = QApplication.instance() or QApplication([])
    user32 = ctypes.windll.user32
    target = QLineEdit()
    target.setWindowTitle("Manuscript insertion probe target")
    target.resize(420, 80)
    target.show()
    target.setFocus()
    user32.SetForegroundWindow(int(target.winId()))
    _pump(app, 0.2)
    snapshot = active_window()
    if snapshot.handle != int(target.winId()):
        print("Could not focus the insertion probe target.")
        target.close()
        return 1

    review = QWidget()
    review.setWindowTitle("Manuscript insertion probe review")
    review.resize(320, 100)
    review.show()
    user32.SetForegroundWindow(int(review.winId()))
    _pump(app, 0.15)

    record = WindowsTextInserter().insert_into(snapshot, "Manuscript insertion probe")
    _pump(app, 0.35)
    value = target.text()
    target.close()
    review.close()
    print(f"inserted={record is not None} text={value!r}")
    return 0 if record is not None and value == "Manuscript insertion probe" else 1


if __name__ == "__main__":
    raise SystemExit(main())
