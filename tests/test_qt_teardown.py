"""Native teardown must remain safe when Qt lists child windows first."""
from pathlib import Path
import os
import subprocess
import sys


def test_child_first_widget_order_exits_cleanly(tmp_path):
    root = Path(__file__).resolve().parents[1]
    code = """
import ctypes
ctypes.windll.kernel32.SetErrorMode(3)
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "tests"))
import conftest
from PySide6.QtWidgets import QApplication
from echo_link_app.window import EchoLinkWindow
from echo_link_core.storage import EchoLinkStore
app = QApplication([])
window = EchoLinkWindow(EchoLinkStore(Path(sys.argv[1]) / "test.db"), enable_hotkey=False, enable_tray=False)
widgets = app.topLevelWidgets()
widgets.sort(key=lambda widget: isinstance(widget, EchoLinkWindow))
app.topLevelWidgets = lambda: widgets
conftest.pytest_unconfigure(None)
print("CLEAN_QT_EXIT")
"""
    env = dict(os.environ, MANUSCRIPT_STATE_DIR=str(tmp_path / "state"),
               ECHOLINK_TEST_TEMP=str(tmp_path / "temp"), ECHOLINK_DISABLE_SOUNDS="1",
               ECHOLINK_DISABLE_MEDIA_CONTROL="1")
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)],
                            cwd=root, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CLEAN_QT_EXIT" in result.stdout
