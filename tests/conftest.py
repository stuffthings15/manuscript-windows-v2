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
