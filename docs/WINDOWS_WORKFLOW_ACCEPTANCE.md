# Windows Dictation Workflow Acceptance

Run this after focused tests and before packaging:

```powershell
.\.venv\Scripts\python.exe .\tools\windows_workflow_probe.py `
  --hotkey ctrl+win `
  --output .\docs\qa\windows-workflow-evidence.json `
  --screenshot .\docs\qa\echo-link-workflow-hud.png
```

Close any other running Echo-Link instance before using the default `Ctrl+Win` shortcut. The probe is local and non-destructive. It briefly opens the default microphone, retains only sample counts and levels, generates a temporary Windows synthetic-voice clip, registers the real hook, shows the Echo Bar, plays short start/stop tones, runs the installed local Whisper model, and inserts the corrected result into a separate helper process. It then proves Direct Unicode mode with accented, CJK, and newline text while confirming that mode never changes the clipboard. Supplementary characters such as emoji must be rejected before typing and routed to Auto or Clipboard mode.

A second phase stops the first listener, disables insertion and sound, and registers a fresh hook. Its press callback deliberately waits 500 ms while the physical `Ctrl+Win` keys are tapped and released in under 100 ms. The evidence must show `press` before `release` on one callback thread, `listening` before `transcribing`, a terminal `complete` state, an idle engine, a stopped recorder, and unchanged target text. Parallel callbacks fail this check by losing the early release and leaving recording stuck. The helper and all temporary audio/database files are removed. The original clipboard text must be restored for the run to pass.

The JSON records every required check, the Git state, harness hash, normal and fast-tap state sequences, callback order/thread, model/device, transcription, inserted text, cue result, HUD state, microphone readiness, latency, and explicit limits. A passing result proves the deterministic Windows workflow, serialized quick-tap handling, and a live microphone stream without asking a person to speak.

It does not prove real spoken-word accuracy, Bluetooth playback, or compatibility with Word, chat, terminals, and elevated windows. Keep those as explicit human acceptance gates.

## Packaged executable

After a clean build, run the same exact packaged path without touching live Echo-Link data:

```powershell
.\dist\EchoLink\EchoLink.exe --workflow-probe `
  --probe-output .\docs\qa\packaged-windows-workflow-evidence.json `
  --probe-screenshot .\docs\qa\echo-link-packaged-workflow-hud.png `
  --probe-hotkey ctrl+alt+f11 `
  --probe-timeout 90
```

This isolated probe blocks sockets, uses fixed Windows synthetic speech, never opens the live microphone, and inserts into a separate helper process. Exit code `0` and every listed required check must be true. It complements the source probe; it does not replace the source probe's live microphone-stream check.

## Installer lifecycle

Close Echo-Link and remove any existing test installation, then run:

```powershell
.\.venv\Scripts\python.exe .\tools\installer_lifecycle_probe.py `
  --installer .\installer-output\EchoLinkSetup.exe `
  --output .\docs\qa\installer-lifecycle-evidence.json `
  --work-root $env:LOCALAPPDATA\Temp
```

The probe refuses a running or installed Echo-Link instance. It uses a generated Temp install root and isolated `ECHOLINK_DATA_DIR`, blocks model-network fallbacks, and records no dictated or clipboard text. A pass requires all 21 checks: bundled required runtimes, install, packaged smoke/diagnostics/workflow, repair, uninstall, private-data preservation, reinstall, second smoke, final uninstall, registry cleanup, and Temp cleanup.

This is same-machine current-user proof. It does not replace a clean-machine run, signed-artifact checks, or versioned upgrade/rollback testing.
