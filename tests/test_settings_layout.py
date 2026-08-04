from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("scale", ["1", "1.25", "1.5", "2"])
def test_local_models_layout_stays_reachable_at_supported_scale(scale, tmp_path):
    probe = textwrap.dedent(
        """
        import os
        from pathlib import Path

        from PySide6.QtCore import QPoint, QRect
        from PySide6.QtWidgets import QApplication

        from echo_link_app.settings_dialog import SettingsDialog
        from echo_link_app.theme import apply_dark_theme
        from echo_link_app.window import EchoLinkWindow
        from echo_link_core.storage import EchoLinkStore

        app = QApplication([])
        app.setStyle("Fusion")
        apply_dark_theme(app)
        store = EchoLinkStore(Path(os.environ["ECHOLINK_TEST_DB"]))
        window = EchoLinkWindow(store, enable_hotkey=False)
        dialog = SettingsDialog(store, window.engine, window)
        dialog.resize(660, 520)
        dialog.show()
        dialog.settings_tabs.setCurrentIndex(2)
        app.processEvents()

        scroll = dialog.settings_scrolls["Local AI"]
        assert scroll.horizontalScrollBar().maximum() == 0
        assert dialog.model_combo.width() > 0
        assert dialog.device_combo.width() > 0
        buttons = [
            dialog.model_folder_button,
            dialog.import_model_button,
            dialog.remove_model_button,
            dialog.use_recommended_button,
            dialog.speed_check_button,
            dialog.check_button,
        ]
        for button in buttons:
            needed = button.fontMetrics().horizontalAdvance(button.text()) + 28
            assert button.width() >= needed, button.text()
            scroll.ensureWidgetVisible(button, 8, 8)
            app.processEvents()
            top_left = button.mapTo(scroll.viewport(), QPoint(0, 0))
            rect = QRect(top_left, button.size())
            assert scroll.viewport().rect().contains(rect), button.text()

        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        app.processEvents()
        bottom_left = dialog.check_button.mapTo(scroll.viewport(), QPoint(0, 0))
        assert scroll.viewport().rect().contains(QRect(bottom_left, dialog.check_button.size()))
        dialog.close()
        window.close()
        """
    )
    env = os.environ.copy()
    env.update(
        {
            "QT_QPA_PLATFORM": "offscreen",
            "QT_SCALE_FACTOR": scale,
            "ECHOLINK_DISABLE_HOTKEY": "1",
            "ECHOLINK_DISABLE_TRAY": "1",
            "ECHOLINK_DISABLE_SOUNDS": "1",
            "ECHOLINK_DISABLE_MEDIA_CONTROL": "1",
            "ECHOLINK_TEST_DB": str(tmp_path / f"settings-{scale}.db"),
            "ECHOLINK_DATA_DIR": str(tmp_path / f"private-{scale}"),
            "PYTHONPATH": str(ROOT / "src")
            + os.pathsep
            + os.environ.get("PYTHONPATH", ""),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    completed = subprocess.run(
        [sys.executable, "-B", "-c", probe],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
