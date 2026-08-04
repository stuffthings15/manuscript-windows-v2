from __future__ import annotations

import ctypes
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from echo_link_core.subscription import SubscriptionStateStore


def main() -> int:
    state = SubscriptionStateStore().load()
    if state is None:
        raise SystemExit("No protected Manuscript developer key was found.")
    ctypes.windll.user32.MessageBoxW(
        None,
        f"{state.license_key}\n\nPrivate developer key for this Windows user and PC.",
        "Manuscript Developer Key",
        0x40,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
