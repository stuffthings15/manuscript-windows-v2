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
