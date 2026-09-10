# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules


ROOT = Path(SPECPATH)


def _build_channel() -> str:
    """Which kind of build this is, from the manifest build.py already wrote.

    tools/build.py calls _write_build_info before PyInstaller runs, so this
    file exists by the time the spec is evaluated. Missing or unreadable is
    treated as production, because the safe default for an unknown build is
    the one that ships less.
    """
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
            # THE TEST HARNESS SHIPS ONLY IN A TESTING BUILD.
            #
            # tools/windows_workflow_probe.py is not a feature. It synthesizes
            # keystrokes through user32.keybd_event, spawns a second process,
            # opens the microphone and writes screenshots to disk. It has no
            # business on a writer's machine.
            #
            # It was in EVERY build until 2026-09-09, and the artifact of
            # 2026-08-16 shipped it reachable: the runtime gate in main.py
            # permits internal commands when subscription_required is false,
            # and that build's subscription.json said exactly that.
            #
            # It cannot simply be dropped, because
            # tools/installer_lifecycle_probe.py drives --workflow-probe
            # against the INSTALLED executable - that is how release
            # acceptance checks the packaged hotkey, HUD, transcription and
            # insertion path end to end. So a testing build keeps it and a
            # production build does not, which is a property of the build
            # rather than of a file a user could edit.
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

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ManuscriptV2",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ROOT / "resources" / "manuscript.ico"),
    version=str(ROOT / "packaging" / "manuscript_version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ManuscriptV2",
)
