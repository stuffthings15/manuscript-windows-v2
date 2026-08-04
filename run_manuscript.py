from pathlib import Path
import os
import sys


PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = PROJECT_ROOT / "src"
if not getattr(sys, "frozen", False):
    marker = PROJECT_ROOT / ".manuscript-canonical-root"
    try:
        marker_value = marker.read_text(encoding="utf-8").strip()
    except OSError:
        marker_value = ""
    if (
        marker_value != "manuscript-windows-canonical-v1"
        or PROJECT_ROOT.name.casefold() != "windows"
        or PROJECT_ROOT.parent.name.casefold() != "manuscript"
        or not (SOURCE_ROOT / "echo_link_core" / "polish.py").is_file()
    ):
        raise SystemExit(
            "Launch refused. Use the canonical Manuscript/Windows project root."
        )
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

# This launcher is the permanent local development entry point. Frozen sale
# builds ignore this flag and enforce their signed subscription profile.
if not getattr(sys, "frozen", False):
    os.environ.setdefault("MANUSCRIPT_TEST_BYPASS_SUBSCRIPTION", "1")

from echo_link_app.main import main


if __name__ == "__main__":
    raise SystemExit(main())
