from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from echo_link_core.subscription import SubscriptionStateStore


ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _development_key() -> str:
    raw = os.urandom(20)
    bits = "".join(f"{byte:08b}" for byte in raw)
    compact = "".join(ALPHABET[int(bits[index:index + 5], 2)] for index in range(0, 160, 5))
    return "MSCR-" + "-".join(compact[index:index + 8] for index in range(0, 32, 8))


def _replace_secret(path: Path, name: str, value: str) -> None:
    text = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    line = f"{name}={value}"
    pattern = re.compile(rf"^{re.escape(name)}=.*$", re.MULTILINE)
    text = pattern.sub(line, text) if pattern.search(text) else f"{text.rstrip()}\n{line}\n"
    path.write_text(text, encoding="utf-8")


def main() -> int:
    key = _development_key()
    state = SubscriptionStateStore().save(key)
    vars_path = Path(__file__).resolve().parents[1] / "website" / ".dev.vars"
    _replace_secret(vars_path, "DEVELOPMENT_LICENSE_HASH", hashlib.sha256(key.encode()).hexdigest())
    _replace_secret(vars_path, "DEVELOPMENT_DEVICE_HASH", state.device_hash)
    print(f"Developer key protected for this Windows user at: {SubscriptionStateStore().path}")
    print("Only one-way key and device hashes were added to the ignored website secrets file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
