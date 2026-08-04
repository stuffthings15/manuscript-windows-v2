from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path


HOST_NAME = "com.manuscript.v2.polish"
EXTENSION_ID_PATTERN = re.compile(r"^[a-p]{32}$")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Register the local Manuscript browser Native Messaging host"
    )
    result.add_argument(
        "--app-root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "dist" / "ManuscriptV2",
    )
    result.add_argument("--uninstall", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    if os.name != "nt":
        raise SystemExit("Browser host registration requires Windows.")
    import winreg

    args = parser().parse_args(argv)
    app_root = args.app_root.resolve(strict=True)
    extension_root = app_root / "browser-extension"
    host_path = (app_root / "browser-host" / "ManuscriptV2BrowserHost.exe").resolve(
        strict=True
    )
    extension_id = (extension_root / "extension-id.txt").read_text(
        encoding="ascii"
    ).strip()
    if EXTENSION_ID_PATTERN.fullmatch(extension_id) is None:
        raise SystemExit("The Manuscript browser extension id is invalid.")
    if app_root not in host_path.parents or host_path.is_symlink():
        raise SystemExit("The Manuscript browser host path is unsafe.")
    manifest_path = extension_root / f"{HOST_NAME}.json"
    registry_paths = (
        rf"Software\Google\Chrome\NativeMessagingHosts\{HOST_NAME}",
        rf"Software\Microsoft\Edge\NativeMessagingHosts\{HOST_NAME}",
    )
    if args.uninstall:
        for registry_path in registry_paths:
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, registry_path)
            except FileNotFoundError:
                pass
        manifest_path.unlink(missing_ok=True)
        return 0

    manifest_path.write_text(
        json.dumps(
            {
                "name": HOST_NAME,
                "description": "Manuscript local writing correction host",
                "path": str(host_path),
                "type": "stdio",
                "allowed_origins": [f"chrome-extension://{extension_id}/"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    for registry_path in registry_paths:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            registry_path,
            0,
            winreg.KEY_WRITE,
        ) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest_path))
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
