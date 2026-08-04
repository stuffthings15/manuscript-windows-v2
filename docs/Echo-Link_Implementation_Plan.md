# Echo-Link Standalone Implementation Plan

> Historical Windows MVP plan. The current local-only, four-platform direction is in `Echo-Link_Product_Roadmap.md`. Cloud inference, cloud accounts, cloud sync, and paid API dependencies are no longer allowed.

## Goal

Build Echo-Link as a focused Windows voice-to-text application in `C:\Users\stuff\Documents\Echo-Link`. It must start quickly, stay quiet when idle, insert polished text into normal Windows applications, preserve the speaker's meaning, and remain independently maintainable.

Echo-Link will be a real standalone product, not a legacy application window with most tabs hidden.

## Current Project Review

### What already works

- `core/audio_recorder.py` provides microphone capture and a 500 ms pre-roll buffer.
- `core/transcriber.py` provides local faster-whisper transcription, CPU and GPU modes, model caching, language selection, and warmup.
- `core/pipeline.py` handles push-to-talk, streaming transcription, active-window context, cleanup, insertion, history, and latency events.
- `cleanup_engine.py` provides deterministic repetition removal, custom dictionary replacement, snippets, and basic self-correction handling.
- `core/ollama_cleaner.py` and `prompts/cleanup_prompts.py` provide optional local grammar, punctuation, and style correction.
- `core/text_inserter.py` preserves the clipboard and inserts text through Windows input APIs.
- The existing `dictation` product profile already limits visible tabs to Dictation and Settings.
- The focused logic baseline passed 51 tests during this review.

### Blocking problems

1. The current standalone profile is not isolated. `app/gui_main.py` still creates editor databases, generation services, AI Generate, Full AI Video Maker, Full AI Song Maker, chat, and the full Settings Hub before it filters visible tabs.
2. The focused standalone GUI test crashed with a Windows access violation while constructing `ui/generate_panel.py`. A background audio connection checker was also active. This proves hidden tabs still affect startup stability.
3. `app/settings_window.py` is about 4,500 lines and exposes video, TTS, image, cloud-generation, Auto-Editor, and AI Addons controls that Echo-Link does not need.
4. The generated legacy dictation PyInstaller spec collects unrelated packages, cloud generation providers, TTS workers, and media assets. The installer is therefore not a small dictation product.
5. Legacy branding and icons are not acceptable for the standalone Echo-Link product.
6. The default `fast` cleanup mode does not run the grammar model. It pastes deterministic cleanup only. The `refine` mode can skip the correction when the user types or changes windows. The `full` mode waits for the model and can feel slow.
7. Existing tests prove control flow and safety, but there is no locked speech corpus that scores word accuracy, punctuation, grammar, protected terms, semantic preservation, and end-to-end latency.
8. Legacy settings use `%LOCALAPPDATA%\LocalDictation\dictation.db`. Echo-Link needs its own data boundary and a selective migration so unrelated credentials and settings do not enter the new product.

## Product Decisions

- Public name: **Echo-Link**.
- Technical name: `EchoLink`.
- Executable: `EchoLink.exe`.
- Installer: `EchoLinkSetup.exe`.
- Install folder: `%ProgramFiles%\Echo-Link`.
- Data folder: `%LOCALAPPDATA%\Echo-Link`.
- Default mode: local-first, dark mode, push-to-talk enabled, history off.
- Large or optional models: installed only through an explicit setup choice.
- No cloud transcription, proofreading, account, sync, hosted inference, or paid API dependency.
- No video, image, music, browser, marketplace, revenue, Hermes, OpenClaw, or QtWebEngine dependencies in the Echo-Link package.

No speech system can guarantee every punctuation choice in every accent, noise level, or ambiguous sentence. Echo-Link should not make a false perfection claim. It will target zero known punctuation defects on its locked release corpus, enforce structural punctuation rules, protect meaning and named terms, and show uncertainty instead of silently inventing text.

## Shared-Code Architecture

Do not copy the current dictation files into a permanent fork.

Create a versioned shared package inside the Echo-Link project:

```text
C:\Users\stuff\Documents\Echo-Link\
  pyproject.toml
  src\echo_link_app\
  packages\echo_link_core\src\echo_link_core\
  resources\
  packaging\
  tests\
  docs\
```

`echo_link_core` owns audio capture, transcription, correction, vocabulary, insertion, hotkeys, session state, and metrics. Echo-Link consumes it directly. Any approved legacy adapter must use a pinned wheel and thin compatibility facades without requiring either executable to access the other source tree at runtime.

### Proposed shared modules

- `echo_link_core/audio_capture.py`: device selection, pre-roll, recording, Bluetooth handling, level meter.
- `echo_link_core/transcription.py`: faster-whisper model lifecycle, streaming partials, final decode, language routing.
- `echo_link_core/correction.py`: correction policy, protected entities, grammar pass, semantic guard, fallback.
- `echo_link_core/punctuation.py`: sentence boundary and punctuation restoration with deterministic validation.
- `echo_link_core/vocabulary.py`: custom words, spoken-form mappings, per-app terms, import/export.
- `echo_link_core/insertion.py`: clipboard-safe paste, direct-input fallback, undo token, target-window checks.
- `echo_link_core/hotkeys.py`: registration, conflict detection, press/release state, recovery.
- `echo_link_core/session.py`: bounded state machine from idle through inserted or failed.
- `echo_link_core/settings.py`: typed settings schema and migrations.
- `echo_link_core/metrics.py`: local latency and quality evidence without recording user text by default.

### Legacy compatibility

- Keep `core.pipeline.run_dictation_pipeline` as a stable facade during migration.
- Keep legacy `LocalDictation` storage unchanged.
- Add contract tests that run the same shared engine through Echo-Link and approved legacy adapters.
- Publish a local wheel for each promoted `echo_link_core` version and pin its hash in both build systems.
- Remove the old dictation installer only after Echo-Link passes migration and clean-machine release checks.

## Correction Pipeline

### 1. Capture and speech detection

- Keep the pre-roll buffer so the first word is not clipped.
- Add microphone level, clipping, silence, and device-loss states.
- Separate recording from UI threads.
- Stop all warmup, audio checks, and model activity when Echo-Link is idle.

### 2. Stable streaming recognition

- Use faster-whisper through CTranslate2.
- Produce partial text while the user speaks, but commit only stable segments.
- Run a final decode on the uncommitted tail after hotkey release.
- Reuse the loaded model and avoid repeated GPU loads.
- Provide an automatic CPU fallback when CUDA is unavailable or busy.

### 3. Vocabulary protection

- Apply longest-match spoken-form replacements.
- Protect names, acronyms, product terms, paths, URLs, email addresses, numbers, and code tokens before grammar correction.
- Support global and per-application dictionaries.
- Add import, export, duplicate detection, and a quick `Add correction` action from recent text.

### 4. Punctuation and grammar

- Run deterministic spacing, quote, apostrophe, number, and duplicate-punctuation repair first.
- Use a small local punctuation-restoration model or a benchmarked local text model for sentence boundaries.
- Run grammar correction only on normal prose. Technical and terminal modes use stricter preservation rules.
- Compare corrected output with the transcript. Reject changes that alter protected entities, numbers, URLs, code, or unsupported facts.
- When confidence is low, keep the safer transcript and mark the segment for review.

### 5. Fast finalization

- Do most correction while the user is still speaking.
- After hotkey release, finalize the tail, validate it, and insert once.
- Keep three clear modes:
  - `Instant`: fastest local transcription plus deterministic cleanup.
  - `Polished`: local punctuation and light grammar with meaning protection. Recommended default after benchmarks pass.
  - `Exact`: technical vocabulary and syntax preservation with minimal rewriting.
- Do not depend on delayed in-place replacement for the normal path.

### 6. Safe insertion and undo

- Verify the foreground target before inserting.
- Preserve and restore clipboard contents.
- Provide a direct-input fallback for applications that block clipboard paste.
- Keep one-click undo for the last Echo-Link insertion.
- Explain elevated-window limitations and offer a safe recovery path.

## User Interface

Build a dedicated PySide6 window. Do not use a legacy tab shell.

### Main screen

- Header: Echo-Link name, listener state, microphone, and settings icon.
- Primary control: one stable Start/Stop Listening control with clear recording state.
- Hotkey line: current push-to-talk shortcut and a change action.
- Mode control: compact segmented control for Instant, Polished, and Exact.
- Live area: waveform/level meter, current phase, partial transcript, and final result.
- Recent text: last few insertions with Copy, Undo, and Add Correction actions.
- Status footer: local model, device, privacy state, and last latency.

### Settings

- General: startup, tray behavior, sound, theme.
- Audio: input device, level test, pre-roll.
- Recognition: language, model, CPU/GPU, streaming.
- Writing: correction strength, style, punctuation, protected content.
- Vocabulary: words, phrases, app-specific terms, import/export.
- Shortcuts: push-to-talk, listener toggle, undo, command mode if retained.
- Privacy: history, audio retention, local/cloud status, clear data.
- Diagnostics: microphone test, model check, hotkey check, insertion test, support bundle.

### Design rules

- Dark mode by default with strong light-on-dark contrast.
- No fixed-height forms that can overlap.
- Scroll when content grows.
- No emoji or ASCII symbols as UI icons; use one consistent icon library.
- Verify at 100%, 125%, 150%, and 200% Windows scaling.
- Verify keyboard-only use, focus order, accessible names, long labels, and compact screens.
- Error text must name the failed stage and the next action.

## Data and Privacy

- Create a new Echo-Link SQLite database with a versioned schema.
- On first run, offer a selective import from `LocalDictation`.
- Import only approved dictation settings, dictionaries, snippets, and optional history.
- Do not import API keys, provider credentials, video settings, or unrelated business data.
- Back up before migration. Keep the old database untouched.
- Encrypt Echo-Link secrets with a new product-specific identifier.
- Store text history and audio only when the user turns those options on.

## Build and Installer

- Create an allowlist-based `packaging/EchoLink.spec` instead of deriving it from `VEdit-AI.spec`.
- Include only PySide6 Core/Gui/Widgets, audio libraries, faster-whisper, CTranslate2, the chosen model files, SQLite support, and Echo-Link resources.
- Exclude QtWebEngine, multimedia video components, ComfyUI, TTS, cloud media providers, Google APIs, editor/render modules, Hermes, OpenClaw, and unrelated business modules.
- Create `packaging/EchoLink.iss` with unique AppId, AppUserModelID, mutex, install folder, startup entry, repair, uninstall, and per-user data preservation choices.
- Sign the executable and installer before commercial release.
- Test install, upgrade, repair, uninstall, and reinstall on a clean Windows machine.

## Implementation Phases

### Phase 0: Freeze and measure

- Save the current 51-test logic baseline and the standalone GUI crash trace.
- Record startup time, idle CPU/GPU, model load time, transcription time, correction time, and insertion time.
- Build a consented benchmark corpus covering short notes, paragraphs, questions, names, acronyms, numbers, URLs, technical text, accents, noise, and self-corrections.
- Lock expected text and scoring rules before tuning.

### Phase 1: Extract `echo_link_core`

- Move one subsystem at a time behind compatibility facades.
- Add contract tests before each move.
- Keep approved legacy-adapter behavior unchanged.
- Verify no model or GPU work starts while idle.

### Phase 2: Build the Echo-Link shell

- Add product identity, dedicated startup, single-instance handling, tray, main window, and focused settings.
- Add the new data store and selective migration preview.
- Add dark-mode layout tests and screenshots.

### Phase 3: Improve correction quality

- Add punctuation restoration, protected-entity comparison, semantic guards, and confidence fallback.
- Benchmark candidate local models by quality, latency, memory, and power.
- Promote only candidates that beat the locked champion without regressions.

### Phase 4: Harden Windows behavior

- Test Notepad, Word, Outlook, browsers, chat apps, IDEs, remote desktop, terminals, elevated windows, Bluetooth devices, USB microphones, sleep/wake, device changes, and multiple keyboard layouts.
- Add crash recovery, stale-hotkey cleanup, clipboard recovery, and support diagnostics.

### Phase 5: Package and release

- Build the minimal executable and installer.
- Verify dependency closure on a clean machine with no development tools installed.
- Run signing, malware scan, upgrade, repair, uninstall, and rollback checks.
- Release Echo-Link only after every blocking gate passes.

## Verification Matrix

### Text quality

- Word error rate.
- Punctuation precision, recall, and sentence-boundary accuracy.
- Grammar error count.
- Exact match for protected vocabulary, numbers, URLs, email addresses, paths, and code.
- Semantic preservation and hallucination detection.
- Manual correction rate on real usage samples.

### Performance

- Press-to-listening delay.
- Hotkey-release-to-insertion delay.
- Model warm and cold behavior.
- CPU, GPU, RAM, and power while active and idle.
- Long-session memory stability.

### Reliability

- Repeated launch/exit and sleep/wake soak.
- Microphone disconnect/reconnect.
- Hotkey conflicts and stuck modifiers.
- Clipboard failure and target-window changes.
- Crash during recording, transcription, correction, insertion, and migration.
- No data loss and no silent duplicate insertion.

### GUI

- No overlap, clipping, hidden text, or covered actions.
- Common desktop sizes and Windows scaling levels.
- Dark mode, long labels, empty states, loading states, and errors.
- Keyboard navigation and screen-reader names.

### Release gates

- All focused unit, integration, GUI, packaging, and clean-machine tests pass.
- Zero blocker defects on the locked benchmark and hardware matrix.
- No protected-token or meaning-changing regression.
- No heavy process, model, or GPU load while idle.
- Installer repair and uninstall are verified.
- Legacy dictation contract tests still pass against the promoted shared-core version.

## Rollback

- Keep the current legacy dictation engine and installer available until Echo-Link is promoted.
- Version every database migration and shared-core wheel.
- Never delete or overwrite the legacy `LocalDictation` database.
- If a shared-core release fails, pin Echo-Link and any approved legacy adapter to the prior signed wheel.
- Keep generated installers and checksums for the last known-good release.

## Out of Scope for the First Release

- macOS and Linux packaging.
- Meeting recording or multi-speaker transcription.
- Automatic cloud use or paid API calls.
- Browser automation, account creation, posting, or revenue workflows.
- Medical, legal, or compliance-grade accuracy claims.

## First Implementation Milestone

The first code milestone is complete only when a dedicated Echo-Link window launches without importing `ui.generate_panel`, `ui.full_ai_song_maker_panel`, editor services, QtWebEngine, ComfyUI, Hermes, or OpenClaw; records one local test utterance; corrects and inserts it; exits cleanly; and leaves the GPU idle afterward.
