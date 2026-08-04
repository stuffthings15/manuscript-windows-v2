from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from echo_link_core.polish_runtime_packs import RuntimePackManager
from echo_link_core.polish_types import RuntimeBackend


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install one signed Manuscript llama.cpp runtime pack"
    )
    parser.add_argument(
        "--backend",
        choices=("cpu", "cuda", "vulkan", "hip", "sycl"),
        required=True,
    )
    parser.add_argument(
        "--consent",
        action="store_true",
        help="Confirm the displayed signed runtime download",
    )
    args = parser.parse_args()
    manager = RuntimePackManager()
    pack = manager.select(RuntimeBackend(args.backend))
    print(
        f"Selected {pack.display_name}: "
        f"{pack.download_size_bytes / (1024**2):.0f} MB",
        flush=True,
    )
    last_report = 0.0

    def progress(filename: str, downloaded: int, total: int) -> None:
        nonlocal last_report
        now = time.monotonic()
        if now - last_report < 1.0 and downloaded < total:
            return
        last_report = now
        percent = downloaded * 100 / max(1, total)
        print(f"{filename}: {percent:.1f}%", flush=True)

    installed = manager.ensure(
        RuntimeBackend(args.backend),
        consent=bool(args.consent),
        progress=progress,
    )
    probed = manager.probe(installed, timeout=30.0)
    print(f"Installed {probed.pack_id}", flush=True)
    print(probed.probe_detail, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
