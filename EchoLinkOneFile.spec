# -*- mode: python ; coding: utf-8 -*-
"""A single self-contained ManuscriptV2.exe, built on request 2026-09-24.

This is NOT the shipping package. EchoLink.spec is, and it stays onedir. This
file exists beside it rather than adding a flag to it, because the two differ
in what the product can do, not just in how the bytes are arranged.

What a onefile build gives up, measured before it was written:

- The browser extension, entirely. Chrome and Edge native messaging needs a
  stable absolute path registered under HKCU, and packaging/EchoLink.iss:67
  writes exactly that at post-install from {app}. A onefile exe has no
  installer to write the keys, and the bootloader unpacks to a fresh random
  %TEMP% directory on every launch, so no stable path exists to register even
  if something did write them. ManuscriptV2BrowserHost is therefore excluded -
  bundling 122MB that provably cannot be reached would be dead weight.
- Cold start. The bootloader extracts roughly 590MB to %TEMP% on EVERY launch
  and deletes it on exit, where onedir pages the same files off disk once.

Dictation, Word insertion, the HUD and the writing palette are untouched:
none of them route through the browser host, and resource_root() in
echo_link_core/paths.py already prefers sys._MEIPASS, which is what onefile
sets, so bundled models and resources resolve without any code change.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules


ROOT = Path(SPECPATH)


def _build_channel() -> str:
    import json

    try:
        manifest = json.loads(
            (ROOT / "build" / "generated" / "build_info.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError):
        return "production"
    return str(manifest.get("build_channel") or "production")


IS_TESTING_BUILD = _build_channel() == "testing-unlocked"
datas = [
    (str(ROOT / "resources" / "manuscript.ico"), "resources"),
    (str(ROOT / "resources" / "manuscript.png"), "resources"),
    (str(ROOT / "resources" / "manuscript.svg"), "resources"),
    (str(ROOT / "resources" / "polish"), "resources/polish"),
    (str(ROOT / "build" / "generated" / "polish-host"), "resources/polish-host"),
    (str(ROOT / "models" / "base.en"), "models/base.en"),
    (str(ROOT / "models" / "polish"), "models/polish"),
    (str(ROOT / "docs" / "capstone" / "gold_corpus.json"), "capstone"),
    (str(ROOT / "build" / "generated" / "build_info.json"), "resources"),
    (str(ROOT / "build" / "generated" / "subscription.json"), "resources"),
]
datas += collect_data_files("faster_whisper")
datas += collect_data_files("ctranslate2")
datas += collect_data_files("sounddevice")
binaries = (
    collect_dynamic_libs("ctranslate2")
    + collect_dynamic_libs("sounddevice")
    + collect_dynamic_libs("sqlcipher3")
    + collect_dynamic_libs("uiautomation")
    + collect_dynamic_libs("onnxruntime")
    + collect_dynamic_libs("winrt")
)
hiddenimports = sorted(
    set(
        collect_submodules("faster_whisper")
        + collect_submodules("ctranslate2")
        + collect_submodules("sqlcipher3")
        + collect_submodules("uiautomation")
        + collect_submodules("onnxruntime")
        + collect_submodules("symspellpy")
        + collect_submodules(
            "comtypes",
            filter=lambda name: not name.startswith("comtypes.test"),
        )
        + collect_submodules("winrt.runtime")
        + collect_submodules("winrt.windows.foundation")
        + collect_submodules("winrt.windows.foundation.collections")
        + collect_submodules("winrt.windows.media.control")
        + [
            "av",
            "tokenizers",
            # Same rule as EchoLink.spec: the keystroke-synthesizing probe
            # ships only in a testing build, never on a writer's machine.
            *(["tools.windows_workflow_probe"] if IS_TESTING_BUILD else []),
            "pyperclip",
            "winrt._winrt",
            "winrt._winrt_windows_foundation",
            "winrt._winrt_windows_foundation_collections",
            "winrt._winrt_windows_media_control",
        ]
    )
)

a = Analysis(
    [str(ROOT / "run_manuscript.py")],
    pathex=[str(ROOT), str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=[
        "IPython",
        "matplotlib",
        "notebook",
        "pandas",
        "pytest",
        "scipy",
        "torch",
        "torchvision",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

# The onefile difference: binaries and datas go INTO the executable, and there
# is no COLLECT step, so nothing is written beside it.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    exclude_binaries=False,
    name="ManuscriptV2",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ROOT / "resources" / "manuscript.ico"),
    version=str(ROOT / "packaging" / "manuscript_version_info.txt"),
)
