# Manuscript

Manuscript is a local-only voice-to-text and writing project. The complete current app is on Windows: hold `Ctrl+Win`, speak, and release, toggle hands-free mode with `Ctrl+Alt+Space`, or highlight text to open the Writing HUD automatically. `Ctrl+Shift+Space` remains the manual fallback. Versioned shared contracts and Android and iOS foundations now start the wider macOS, iOS, and Android product without adding a cloud runtime.

## What is included

- Local faster-whisper speech recognition.
- Fast deterministic punctuation, capitalization, repetition/filler removal, natural backtracking, numbered/bullet lists, and custom vocabulary.
- Optional local Ollama grammar refinement.
- Fast, Developer, Refine, and Accuracy dictation modes.
- Deterministic Developer mode for spoken case, filenames, `@file` tags, syntax, indentation, and CLI text. It never executes commands or calls a writing model.
- Push-to-talk global hotkey.
- Hands-free toggle hotkey, local silence auto-stop, hard recording limit, and Esc discard.
- High-contrast desktop interface with a persistent section rail, global microphone/hotkey/private-paste status, responsive Dictate controls, category-based Settings, and a focus-safe HUD.
- Focus-safe draggable Manuscript Bar with saved placement, selectable local sound cues, output routing, and system tray controls.
- Safe Windows media pause while listening; Manuscript resumes only sessions it paused.
- Optional live local text while speaking and quiet-speech mode.
- Captured-target insertion with Auto, private Clipboard, and clipboard-free Direct Unicode (BMP) modes, explicit access-boundary failures, partial-send protection, and guarded undo. Auto/Clipboard marks staged text to stay out of Windows clipboard history and cloud sync; Direct never stages text on the clipboard. Auto/Clipboard remains the safe path for emoji.
- Separate memory-only Manuscript private paste with `Ctrl+Alt+V`; it never reads or overwrites the Windows clipboard.
- Review-before-insert with raw, corrected, and changed text.
- Automatically appearing, focus-safe Writing HUD for stable selected text with local spelling, grammar, formatting, and clarity suggestions plus proofread, rewrite, shorten, expand, summarize, formal, friendly, and translate actions. Automatic detection uses Windows accessibility data and never reads or changes the clipboard.
- Word and Outlook editor detection with stable UI Automation ranges and a non-mutating exact correction underline.
- Optional packaged Chrome/Edge extension and local Native Messaging host. It appears only beside editable text fields, never in password fields, and requires red-delete/green-insert review before replacement.
- Local readability scoring with grade estimate, sentence/word counts, and concrete writing flags.
- Session-only local source search with path-and-line citations and local corpus similarity checks.
- Session-only chat over approved local `.txt` and `.md` sources, using the current loopback Ollama model with exact path-and-line excerpts shown for review.
- Local document editor with explicit text import/export, guarded unsaved work, and SQLite-backed immutable revision history and restore.
- Writing profiles for general, email, meeting, marketing, technical, and code-safe work.
- Guided offline faster-whisper model-pack import and safe user-pack removal.
- Loopback-only writing-model manager with local GGUF import, exact tag/digest details, SHA256 evidence, model selection, removal confirmation, and a bounded local speed check.
- Optional private learning from explicitly approved short Review edits, with enable, disable, delete, and reset controls.
- Human review plus selection re-check before any selected text is replaced.
- Local snippets and selectable local speech model packs.
- Optional local history. History is off by default.
- CPU mode by default. GPU use is optional and released while idle.
- Local hardware/model recommendation considers only installed models and exposes GPU only when its required runtime libraries load.
- A frozen local-core v1 JSON contract with strict privacy flags, exact golden fixtures, a Python reference adapter, and strict Android and Swift wire bindings that reject unknown fields, missing fields, shape changes, and privacy downgrades.
- A compile-tested Android containing app and input method with strict Android 12+ on-device speech, editable review, encrypted one-use approval, and tap-only keyboard insertion. Physical-device proof remains open.
- Structurally validated macOS and iPhone source foundations: app-owned capture boundaries, a focus-safe macOS status/insertion flow, strict on-device iPhone speech with editable review, a short-lived App Group payload, a no-Open-Access keyboard, and a share extension. Xcode compilation and Apple-device proof remain open.

No cloud service, account, API key, or paid call is required.

## Writing HUD

1. Highlight text in another Windows app.
2. The HUD appears automatically beside a stable accessible selection without taking typing focus. Use `Ctrl+Shift+Space` if that app does not expose its selection to Windows accessibility.
3. Review the immediate local spelling, grammar, formatting, and clarity suggestions.
4. Apply one or all suggestions to the preview, or run a local rewrite/style command.
5. Review the final preview, then replace the unchanged selection or stage it in Manuscript private paste.

Automatic appearance can be disabled in Settings. The manual shortcuts are configurable and cannot overlap.

## Developer dictation

Choose **Developer** under Writing mode. Speak explicit commands such as `camel case user name`, `tag file app slash routes dot ts`, `open paren`, `new line`, and `indent`. Manuscript formats the text locally and inserts it once without Ollama. See `docs\DEVELOPER_MODE.md` for the command reference.

## Documents and approved sources

- Use **Documents** to create or import local `.txt`/`.md` work, edit it, and save explicit revisions. Restoring an old revision creates a new revision; it never rewrites history.
- Use **Sources > Ask sources** to approve local `.txt`/`.md` files for the current session and ask the configured local Ollama model questions. Answers are read-only and show the exact excerpts used.
- Source questions, excerpts, and answers are not saved. Nothing is applied to a document automatically.

See `docs\LOCAL_MODEL_PACKS.md` for adding an English or multilingual speech pack. See `docs\LOCAL_WRITING_MODELS.md` for local Ollama writing-model import and verification, and `docs\WRITING_QUALITY_ACCEPTANCE.md` for the honest fixed-case quality gate. None of these workflows gives Manuscript cloud access.

## Run from source

```powershell
.\scripts\bootstrap.ps1
.\.venv\Scripts\python.exe .\run_echo_link.py
```

The bundled speech model must exist at `models\base.en`. The bootstrap script checks it but does not download it.

## Build

```powershell
$env:MANUSCRIPT_SIGNING_THUMBPRINT = "40_HEX_CHARACTER_CERTIFICATE_THUMBPRINT"
.\.venv\Scripts\python.exe .\tools\build.py --clean --installer
```

The standalone app is written to `dist\Manuscript\Manuscript.exe`. When Inno Setup 6 is installed, the installer is written to `installer-output\ManuscriptSetup.exe`.

Production packaging fails closed without a trusted Windows code-signing
certificate. Every packaged `.exe`, `.dll`, and `.pyd`, followed by the
installer, is SHA-256 signed, timestamped, and verified against the Windows
Authenticode policy. A self-signed development certificate does not satisfy
the public-release gate.

For local testing only, build the visibly unlocked and optionally unsigned
package:

```powershell
.\.venv\Scripts\python.exe .\tools\build.py --clean --installer --testing-unlocked
```

The installer includes the required `base.en` speech model, desktop UI, audio, local speech, and encrypted-database runtimes. Manuscript does not need a first-run download. Optional speech and writing packs are added only through the explicit local import controls.

The installer also registers the current-user browser host. For local testing,
load `dist\Manuscript\browser-extension` as an unpacked extension in Chrome or
Edge. Store publication and signing remain release gates.

The capstone runner is intentionally absent from the consumer navigation. For a supervised local 20-case human run, launch the separate internal surface:

```powershell
.\.venv\Scripts\python.exe .\run_echo_link.py --internal-evaluation
```

The Android product is under `Manuscript\Android`:

```powershell
cd Manuscript\Android
.\gradlew.bat --offline :app:testDebugUnitTest :app:assembleDebug
```

It produces a debug APK only. Physical-device verification, local mobile speech, release signing, and store packaging remain open.

Apple mobile source, XcodeGen configuration, privacy manifests, policies, and validators are under `Manuscript\iOS`. The separate `Manuscript\macOS` folder points to the desktop target in that shared Apple source tree. Both require macOS and Xcode before any compiled-delivery claim.

Linux is under `Manuscript\Linux`. The standalone web app is under `Manuscript\HTML`, with the optional Chrome/Edge bridge under `Manuscript\HTML\BrowserBridge`. See `Manuscript\README.md` for the full platform map.

## Verify

```powershell
.\scripts\verify.ps1
```

Add `-WriteCurrent` to refresh `docs\capstone\current_results.json`. The locked baseline is not changed. Live user acceptance and time metrics are recorded separately in `docs\capstone\manual_review_template.csv`.

Run `tools\windows_workflow_probe.py` for the repeatable local hotkey, cue, Manuscript Bar, microphone-stream, Whisper, insertion, clipboard, and Direct Unicode acceptance check. See `docs\WINDOWS_WORKFLOW_ACCEPTANCE.md`.

The frozen executable also supports `Manuscript.exe --workflow-probe --probe-output <local-json>`. That isolated check blocks sockets, uses fixed synthetic speech, never opens the live microphone, and verifies the packaged hotkey, HUD, cues, local model, captured-window Direct insertion, and that the Windows clipboard stays untouched.

Run `tools\external_insertion_acceptance_probe.py` for isolated external textbox, chat-like textbox, and Windows terminal acceptance. Elevated apps, real third-party chat clients, and physical hardware stay manual gates.

## Privacy and power

- Audio is processed locally. It is retained only when the user explicitly enables encrypted speech improvement logs.
- History is stored only when enabled.
- Encrypted speech improvement logs keep actual PCM audio plus raw, cleanup, suggested, and final transcript stages in the SQLCipher database. They are off by default, visible and reviewable in History, and automatically deleted after 1–15 days.
- Ollama requests use only numeric loopback (`127.0.0.1` or `::1`) on port 11434, bypass proxies, reject redirects, and enforce bounded request/response/time limits. The model unloads after each refinement request.
- The speech model is released while idle.
- First-word protection keeps only the latest 500 ms in memory while the microphone is ready; set it to `0 ms` in Settings to close the idle microphone stream. It reaches disk only as part of an explicitly enabled encrypted speech log.
- Windows media control uses local System Media Transport Control sessions. It does not send playback data anywhere.
- **Stage private paste** stores text only in Manuscript process memory. `Ctrl+Alt+V` inserts it with clipboard-free Direct input. The normal Windows clipboard is unchanged. Direct is the default for new profiles; Windows clipboard compatibility modes are explicit Settings choices.
- Structured exports, imports, model packs, GGUF files, approved sources, plagiarism re-checks, legacy databases, and QA evidence use local-only path guards. Guarded exports add atomic writes, secret redaction, hashes, and content manifests. Exact document exports intentionally preserve user-authored text.
- Windows private storage uses SQLCipher for database/WAL pages and a random key wrapped by current-user DPAPI. Existing plaintext databases migrate under the single-app lock with integrity and schema/value checks before atomic replacement. Explicit document and report exports remain plaintext by user choice.
- Settings provides a typed **Delete all private data** purge. It clears private stores, checkpoints/truncates the WAL, vacuums the database when possible, rotates the active database key, and clears bounded product logs. This is not secure erase of SSD blocks, backups, snapshots, exports, or target-app copies.
- Settings provides explicit Windows data-folder hardening. It removes inherited broad grants and verifies access for the current user, SYSTEM, and local Administrators. Encryption does not stop administrators, same-user malware, live-memory access, or usable copies held by the same Windows profile. See `docs\SECURITY_THREAT_MODEL.md`.
