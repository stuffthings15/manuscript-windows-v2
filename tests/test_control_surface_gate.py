"""The control-surface gate has to fail on a dead control, or it is decoration.

`ui_smoke_probe.py` walked the tabs, printed findings and always returned 0, so
nothing it noticed could stop a release. Replacing it with something that
returns nonzero is only worth anything if the nonzero case actually happens, so
these tests inject the defects it claims to catch and assert it catches them,
and assert the clean window comes back empty so a gate that fails everything
cannot pass for a strict one.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "src", ROOT / "tools"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

# Nothing is set here on purpose. conftest.py already selects the offscreen
# platform and disables the hotkey, and it deliberately leaves the tray alone
# because test_gui asserts the tray works. Setting ECHOLINK_DISABLE_TRAY here
# leaked into the shared pytest process and failed that test two thousand tests
# later, which looked exactly like a product regression.

from PySide6.QtWidgets import QApplication, QCheckBox, QPushButton  # noqa: E402

import control_surface_gate as gate  # noqa: E402
from echo_link_app.window import EchoLinkWindow  # noqa: E402
from echo_link_core.storage import EchoLinkStore  # noqa: E402


@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory(prefix="echo-link-gate-") as directory:
        value = EchoLinkWindow(
            EchoLinkStore(Path(directory) / "echo-link.db"),
            enable_hotkey=False,
            enable_tray=False,
        )
        document = value.document_workspace.create_document(
            "Control surface check",
            "A local document with enough text to select, export and revise.",
        )
        value.document_panel.refresh_documents(select_id=document.id)
        value.resize(1024, 720)
        value.show()
        app.processEvents()
        yield value, app
        value.close()


def test_the_shipped_window_has_no_findings(window):
    """The baseline. Without this a gate that fails everything looks strict."""
    value, app = window

    report = gate.audit(value, app)

    assert report["findings"] == []
    # Guards against the walk quietly checking nothing at all, which is how a
    # green run stops meaning anything.
    assert report["checked"] > 50


def test_a_button_wired_to_nothing_is_caught(window):
    """A control can be present, named and enabled and still be dead.

    This is the defect the old probe could not fail on. Reachability alone
    passes it.
    """
    value, app = window
    dead = QPushButton("Improve everything", value.tabs.widget(0))
    dead.setAccessibleName("Improve everything")
    dead.show()
    app.processEvents()

    findings = gate.audit(value, app)["findings"]

    assert any("Improve everything" in line for line in findings)
    assert any("cannot do anything" in line for line in findings)


def test_a_checkbox_wired_to_nothing_is_not_reported(window):
    """A checkable control is a state holder, not an action.

    SettingsDialog reads fourteen of them with isChecked() when the user saves
    rather than connecting each one. Treating those as dead produced fourteen
    false findings in one run, which is the reason this exemption exists and
    the reason it needs a test of its own.
    """
    value, app = window
    box = QCheckBox("Read on save", value.tabs.widget(0))
    box.setAccessibleName("Read on save")
    box.show()
    app.processEvents()

    findings = gate.audit(value, app)["findings"]

    assert not any("Read on save" in line for line in findings)


def test_a_control_with_no_name_is_caught(window):
    """An unnamed control fails accessibility and cannot be driven at all."""
    value, app = window
    nameless = QPushButton("", value.tabs.widget(0))
    nameless.clicked.connect(lambda: None)
    nameless.show()
    app.processEvents()

    findings = gate.audit(value, app)["findings"]

    assert any("has no name" in line for line in findings)


def test_importing_the_gate_does_not_change_the_environment():
    """Importing this tool must not touch the process it is imported into.

    It sets four ECHOLINK_DISABLE_* variables and the Qt platform when it runs
    as a script, which is right for a script and wrong for an import. At module
    scope they landed in the shared pytest process and disabled the tray for
    every test that built a window afterwards, so
    test_system_tray_keeps_hotkey_service_ready_when_window_closes failed on
    `assert window.tray_enabled` and read as a product defect.

    Checked in a clean subprocess, because by the time this module runs the
    import has already happened and any damage would already be done.
    """
    import subprocess

    code = (
        "import os, sys; sys.path.insert(0, 'tools');"
        "import control_surface_gate;"
        "print(repr(os.environ.get('ECHOLINK_DISABLE_TRAY')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env={k: v for k, v in os.environ.items() if k != "ECHOLINK_DISABLE_TRAY"},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "None", (
        "importing control_surface_gate set ECHOLINK_DISABLE_TRAY in the "
        "importing process"
    )
