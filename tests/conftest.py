from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ECHOLINK_DISABLE_HOTKEY", "1")
os.environ.setdefault("ECHOLINK_DISABLE_MODEL_SETUP", "1")
os.environ.setdefault("ECHOLINK_DISABLE_PALETTE_AI", "1")
TEST_TEMP = Path(os.environ.get("ECHOLINK_TEST_TEMP", ROOT / ".test-data" / "tmp"))
TEST_TEMP.mkdir(parents=True, exist_ok=True)
os.environ["TEMP"] = str(TEST_TEMP)
os.environ["TMP"] = str(TEST_TEMP)
tempfile.tempdir = str(TEST_TEMP)


# The test suite must never write into the live HUD state directory.
#
# The state file and event log are read by tooling and quoted in reports, and a
# suite run was observed overwriting them with fixture values while the real
# app was idle - so a person reading the live state would have been looking at
# a test's numbers. The same class of mistake had already put fixture sessions
# inside the real AI Brain earlier the same day.
#
# Set for the whole session rather than per test, because the tests that need
# isolating are the ones that do not know they are writing state at all: any
# test that renders the palette publishes it.
_STATE_SANDBOX = Path(tempfile.gettempdir()) / "manuscript-test-state"
os.environ.setdefault("MANUSCRIPT_STATE_DIR", str(_STATE_SANDBOX))

# Measurement pins must never reach the suite.
#
# MANUSCRIPT_PREFERRED_PACK and MANUSCRIPT_PREFERRED_MODE exist so one run can
# compare two models. The acceptance tier runs the suite as its first step, in
# the same environment, so a pinned tier ran the whole suite with a pack and a
# tier forced - and every test that asserts the DEFAULT choice failed.
#
# Measured 2026-08-25: pinned tiers reported "the test suite reported 10
# failed" while the same tree passed 2429 standalone, including with the app
# running. Ten failures looked like a product regression and were the
# measurement telling the tests what to think.
#
# Cleared for the whole session rather than per test, because the tests that
# need protecting are the ones with no idea this environment exists.
for _pin in (
    "MANUSCRIPT_PREFERRED_PACK",
    "MANUSCRIPT_PREFERRED_MODE",
    "MANUSCRIPT_TONE_SPAN",
):
    os.environ.pop(_pin, None)


def pytest_unconfigure(config):
    """Destroy GUI fixtures while Python callbacks and Qt are still alive.

    The GUI suite left 2,000 top-level widgets for interpreter finalization.
    Assertions passed, then Qt aborted or hung while destroying them after
    Python teardown had started. Explicit main-thread destruction reproduced
    a clean exit without suppressing exceptions or changing the test result.
    """
    widgets_module = sys.modules.get("PySide6.QtWidgets")
    if widgets_module is None:
        return
    app = widgets_module.QApplication.instance()
    if app is None:
        return
    from PySide6.QtCore import QCoreApplication, QEvent
    from shiboken6 import isValid

    window_module = sys.modules.get("echo_link_app.window")
    window_type = getattr(window_module, "EchoLinkWindow", ())
    widgets = app.topLevelWidgets()
    # Qt does not order this list by ownership. Stop every controller before
    # deleting any palette or bar that a controller's shutdown still uses.
    for widget in widgets:
        if isValid(widget) and isinstance(widget, window_type):
            widget.shutdown()
    for widget in widgets:
        if not isValid(widget):
            continue
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.shutdown()
