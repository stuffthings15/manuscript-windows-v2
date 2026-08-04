# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules


ROOT = Path(SPECPATH)
datas = [
    (str(ROOT / "resources" / "polish"), "resources/polish"),
    (str(ROOT / "models" / "polish"), "models/polish"),
    (str(ROOT / "build" / "generated" / "build_info.json"), "resources"),
    (str(ROOT / "build" / "generated" / "subscription.json"), "resources"),
]
binaries = collect_dynamic_libs("onnxruntime")
hiddenimports = sorted(
    set(
        collect_submodules("onnxruntime")
        + collect_submodules("symspellpy")
        + collect_submodules("tokenizers")
    )
)

analysis = Analysis(
    [str(ROOT / "run_browser_host.py")],
    pathex=[str(ROOT), str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=[
        "PySide6",
        "av",
        "ctranslate2",
        "faster_whisper",
        "matplotlib",
        "numpy.testing",
        "pandas",
        "pytest",
        "scipy",
        "sounddevice",
        "sqlcipher3",
        "torch",
        "torchvision",
        "uiautomation",
        "winrt",
    ],
    noarchive=False,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="ManuscriptV2BrowserHost",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    icon=str(ROOT / "resources" / "manuscript.ico"),
    version=str(ROOT / "packaging" / "manuscript_version_info.txt"),
)

collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="ManuscriptV2BrowserHost",
)
