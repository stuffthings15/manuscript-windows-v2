# Echo-Link Requirements Traceability Matrix

## Status and release codes

- `Done`: implemented in the current Windows MVP.
- `Partial`: some behavior exists but does not meet the full requirement.
- `Capstone`: planned for the three-week proof.
- `Planned`: assigned to a later release.
- `Excluded`: conflicts with the locked local-only boundary.
- Releases: `C0` capstone, `W1` Windows voice, `W2` local writing, `X1` shared core, `M1` macOS, `A1` Android, `I1` iPhone, `B1` browser, `E1` local enterprise.

## Capstone requirements

| ID | Requirement | Status | Evidence or gap | Release | Verification |
|---|---|---|---|---|---|
| C-001 | Define one user and one costly problem | Capstone | User is Curtis; problem is correction work after dictation | C0 | One-page proposal |
| C-002 | Record a real baseline | Capstone | The consumer Capstone tab link remains removed. `run_echo_link.py --internal-evaluation` now opens the separate supervised 20-case runner with local CSV/JSON export; Curtis's live run is still needed | C0 | Internal-launcher GUI test, results JSON, and completed CSV |
| C-003 | Use one or two primary metrics | Done | Runner calculates correction-free acceptance and median time to accepted text | C0 | Evaluation tests and protocol |
| C-004 | Set numeric targets from evidence | Capstone | Targets stay unset until the first live baseline | C0 | Signed result note |
| C-005 | Use MoSCoW scope | Done | Roadmap contains Must, Should, Could, and Won't | C0 | Document review |
| C-006 | Build a thin proof of the riskiest assumption | Partial | Windows pipeline and guided proof runner exist; completed human evidence is missing | C0 | Live 20-case run |
| C-007 | Use GPT project workflow, not Claude | Done | GPT is planning and review only; runtime stays local | C0 | GPT project instructions and files |
| C-008 | Human reviews every output | Done | Review mode and Capstone runner require explicit accept, retry, or reject | C0 | Review and evaluation tests plus live session |
| C-009 | Feed accepted decisions back into work | Done | Edited Accept, Copy, and Insert decisions can propose safe short phrase pairs; the user explicitly checks each pair before it is stored and applied locally | C0 | Proposal, approval, storage, application, reset, and privacy tests |
| C-010 | Three-week execution plan | Done | Week 1 freeze, Week 2 build, Week 3 prove | C0 | Roadmap review |
| C-011 | One-page proposal and demo evidence | Partial | Older brief exists; final measured version is pending | C0 | Final capstone pack |
| C-012 | Explicit Won't-have boundary | Done | Cloud, later platforms, browser, and enterprise excluded from C0 | C0 | Scope audit |

## Voice dictation specification

| ID | Requirement | Status | Evidence or gap | Release | Verification |
|---|---|---|---|---|---|
| V-001 | Local voice capture | Done | `audio.py` uses the selected microphone | C0 | Recorder unit and live mic test |
| V-002 | Global push-to-talk | Done | `hotkeys.py`, default `ctrl+win`; diagnostics now register and remove a real Windows hook | C0 | Hook registration, hotkey tests, and Notepad test |
| V-003 | Local speech recognition | Done | `transcription.py`, faster-whisper, bundled `base.en` | C0 | Offline transcription test |
| V-004 | Streaming partial transcription | Partial | Local previews commit repeated stable prefixes and hold the changing tail; live latency coverage remains | W1 | Stable-prefix and streaming latency tests |
| V-005 | Cross-application insertion | Partial | Captured-target Auto/Clipboard/Direct insertion, modifier release, partial-send protection, undo, and live Word/external textbox/chat-like multiline/Windows terminal passes exist. A separate process-memory Echo-Link buffer stages text and `Ctrl+Alt+V` inserts through Direct mode without Windows clipboard access or fallback; Direct is the new-profile default. Real third-party chat and elevated-app acceptance remain | C0 | private-buffer/GUI/diagnostics/insertion tests, `tools/external_insertion_acceptance_probe.py`, content-free live evidence, and expanded app matrix |
| V-006 | Windows app | Done | PySide6 app, executable, and installer exist; the current installer has a stable AppUserModelID and installer-aware mutex, bundles every required runtime, and passes the same-machine 21/21 lifecycle probe | C0 | Unit tests and `docs/qa/installer-lifecycle-evidence.json`; clean-machine/signing remain release gates |
| V-007 | macOS app | Partial | The former macOS source snapshot is preserved for review but is not an active Manuscript platform folder; Xcode build, native operation execution, signing, shortcuts/menu bar, and live proof remain | M1 | `Manuscript/Archive/legacy-platforms/apple`, archived structural validator, future macOS decision and acceptance suite |
| V-008 | Android app and input method | Partial | The current Android product is organized as the canonical Android source. Physical microphone/OEM/Keystore behavior, signing, and store proof remain | A1 | `Manuscript/Android`, JVM/static tests, build/lint, and `docs/qa/android-foundation-evidence.json` |
| V-009 | iOS app and keyboard/share flow | Partial | The containing app requires Apple's on-device recognizer, exposes permission/model-unavailable states and an editable live/final review draft, then explicitly approves one bounded App Group payload; the read-only no-Open-Access keyboard and share extension never capture audio. Xcode build, signing, provisioning, and device proof remain | I1 | `Manuscript/iOS`, structural validator, Swift contract sources, and future Xcode/device/extension suite |
| V-010 | Filler removal | Partial | Removes vocal fillers, safe punctuated `like`/`you know`, and explicit pause markers while preserving meaningful `like`; live speech variants remain | C0 | Locked and expanded local filler tests plus live speech |
| V-011 | Repetition removal | Done | Adjacent word and phrase repetition cleanup | C0 | Unit and corpus tests |
| V-012 | Spoken punctuation | Done | Comma, period, question, exclamation, colon, semicolon, lines | C0 | Unit and corpus tests |
| V-013 | Automatic punctuation and sentence boundaries | Partial | Final marks, direct questions, explicit period/new sentence, and conservative `now`, `next`, `after that`, and `finally` command boundaries exist; broad ambiguous prose splitting remains intentionally out of scope until human-rated | C0 | Locked and expanded punctuation/run-on cases |
| V-014 | Natural self-correction and backtracking | Partial | `no wait`, `actually no`, `scratch that`, and punctuated `actually, make that` forms pass local regressions; real speech variants remain | C0 | Locked and expanded backtracking cases plus live speech |
| V-015 | Automatic lists | Done | Repeated-line commands, sequential spoken numbers/ordinals, and explicit bullet markers become local bullet lists | C0 | Locked and expanded list cases |
| V-016 | 100+ languages | Partial | Local multilingual pack discovery, selector, and common language choices exist; only English is bundled | W1 | Per-language local model matrix |
| V-017 | Whisper mode | Partial | Quiet-speech gain and lower local VAD threshold exist; live hardware acceptance is pending | W1 | Quiet-speech hardware tests |
| V-018 | Custom dictionary | Done | SQLite vocabulary and settings UI | C0 | Storage and correction tests |
| V-019 | Learned corrections | Done | Optional Review edits propose bounded phrase pairs; unchecked, unsafe, sensitive, broad, rejected, retry, and unchanged edits are not stored; rules can be disabled, deleted, or reset | C0 | Learning, engine, storage, GUI, and privacy tests |
| V-020 | Snippets | Done | Local trigger/content store and exact spoken expansion | C0 | Storage, correction, and GUI tests |
| V-021 | Writing styles | Partial | Fast, deterministic Developer, Refine, Accuracy, and local Ollama options exist; broader prose style quality remains | W2 | Style corpus and meaning guard |
| V-022 | Developer vocabulary | Done | Dedicated deterministic Developer mode normalizes common technical terms and applies the user's local vocabulary without Ollama | W1 | `tests/test_developer_text.py` and `tests/test_correction.py` |
| V-023 | Developer syntax | Done | Explicit local case, syntax-symbol, newline, tab, indent/dedent, operator, and CLI-text commands pass golden tests and never execute text | W1 | `tests/test_developer_text.py`, engine, and privacy tests |
| V-024 | Filename tagging | Done | Explicit `file`/`filename` and `tag file`/`at filename` commands create paths and `@file` tags while preserving existing paths, URLs, and identifiers | W1 | Filename and protected-token golden tests |
| V-025 | Protected names, numbers, URLs, paths, and code | Partial | Locked corpus now preserves all protected tokens; broader real-speech set remains | C0 | Protected-content suite |
| V-026 | Movable Echo Bar | Done | Focus-safe Echo Bar is draggable, clamps to the available screen, saves coordinates, and restores them after restart | C0/W1 | GUI clamp test and live drag/restart probe |
| V-027 | Crash and result recovery | Done | Opt-in stable/final-text checkpoints pass a 12-stage deterministic failure matrix and a 9-stage subprocess crash/relaunch matrix; insertion-boundary crashes are explicitly uncertain and never auto-retried | C0 | `tools/recovery_crash_probe.py`, `tools/reliability_fault_probe.py`, and fault-probe tests |
| V-028 | Accessibility | Partial | Stable accessible names, form-label links, keyboard focus order, screen-reader status announcements, compact layouts, and fresh-process 100/125/150/200% scale probes pass; manual Narrator/NVDA acceptance remains | W1 | `tests/test_accessibility.py`, `tools/accessibility_probe.py`, `docs/ACCESSIBILITY_ACCEPTANCE.md`, and manual screen-reader record |
| V-029 | Optional local history | Done | SQLite history, off by default | C0 | Storage and history-off tests |
| V-030 | Raw audio not retained | Done | Audio is held in memory; every injected failure stage audits its temporary tree and finds no audio file | C0 | Privacy, file, process, and reliability-fault audits |
| V-031 | Cloud sync | Excluded | Conflicts with local-only rule; use explicit local export/import | None | Network and storage audit |
| V-032 | Team dictionary and snippets | Planned | Local packs only | E1 | Multi-profile local tests |
| V-033 | Team analytics | Planned | Local aggregate reports only | E1 | Privacy and report tests |
| V-034 | Activation feedback | Partial | The repeatable full workflow probe plays both local WAV cues and shows the focus-safe HUD; Test controls, output routing, and Auto 700 ms Bluetooth wake delay exist; physical Bluetooth proof remains | C0/W1 | Live cue and Bluetooth hardware tests |
| V-035 | Always-ready tray lifecycle | Done | Show, Dictate, Settings, Quit, and close-to-tray behavior keep the hotkey service active | W1 | Tray lifecycle GUI test |
| V-036 | First-word and duration protection | Partial | Configurable 500 ms memory-only pre-roll, 120-second recording limit, and hands-free silence stop exist; broader live microphone-duration acceptance remains | W1 | Recorder unit, timer, and live microphone-ready tests |
| V-037 | Selectable audio output | Done | System default or a discovered local output device can be selected for WAV cue playback | W1 | Routed playback unit and live default-output tests |
| V-038 | Hands-free dictation | Partial | Separate toggle hotkey, tray action, persistent Echo Bar, energy VAD, 2.5-second silence stop, Esc discard, and hard limit exist; live microphone acceptance remains | W1 | Tracker, engine, live hook, and microphone tests |
| V-039 | Pause/resume playing media | Done | Local Windows SMTC sessions pause only while playing and only owned paused sessions resume | W1 | Ownership tests and live offline Chrome session probe |
| V-040 | Local hardware/model recommendation | Done | Fresh profiles apply a verified installed-model recommendation once; later user choices are preserved; Settings retains one-click selection and an optional local speed check | W1 | Recommendation matrix, one-time apply test, and live local benchmark |
| V-041 | Selected-text cross-app replacement | Partial | Capture, clipboard restore, focus return, and guarded replacement pass in Qt single-line, multiline, rich-text, Chrome contenteditable, and VS Code; Word and chat remain | W2 | Expanded live local Windows matrix probes |

## AI writing platform specification

| ID | Requirement | Status | Evidence or gap | Release | Verification |
|---|---|---|---|---|---|
| W-001 | Grammar correction | Partial | Dictation refinement and selected-text Proofread use local Ollama; a fixed offline benchmark now checks three grammar/punctuation references | C0/W2 | Live local-model and human-rated grammar run |
| W-002 | Spelling correction | Partial | Selected-text Proofread instructs the local model to fix spelling and the fixed grammar set includes typo checks; broader typo coverage remains | W2 | Expanded dictionary and typo corpus |
| W-003 | Punctuation correction | Partial | Basic local rules, exact locked punctuation references, and conservative direct-question/imperative run-on boundaries exist; broad ambiguous prose still needs human-rated coverage | C0/W2 | Expanded punctuation/run-on corpus |
| W-004 | Style and tone | Partial | Formal, Friendly, and profile commands exist; three fixed style/tone cases have deterministic acceptance checks, while human review remains | W2 | Human-rated style set |
| W-005 | Readability feedback | Done | Selected text receives a deterministic local score, grade estimate, counts, and concrete writing flags without a model or network | W2 | Fixed scoring examples and GUI test |
| W-006 | Rewrite, shorten, and expand | Partial | Local commands, review, protected tokens, guarded replacement, and three fixed meaning-preservation cases exist; broader live quality remains | W2 | Live local-model and human meaning review |
| W-007 | Summarization | Partial | Local summary command plus three fixed key-fact, number, and length cases exist | W2 | Live local-model document benchmark |
| W-008 | Translation | Partial | Local translation plus three fixed French, Spanish, and German marker/protected-value cases exist; fluent human language review remains | W2 | Live local-model and human language benchmark |
| W-009 | Citations | Done | User-approved `.txt` and `.md` sources are indexed in memory and return exact local path-and-line citations | W2 | Ranking, path, line, revoke, format, size, binary, and GUI tests |
| W-010 | Plagiarism checking | Done | Selected text is compared only with user-approved local `.txt` and `.md` files; matched passages and bounded similarity are reported with no network access | W2 | Local match, coverage, path-safety, size, binary, and GUI tests |
| W-011 | Writing chat | Done | Sources > Ask sources uses only explicitly approved session-memory `.txt`/`.md` files and a loopback Ollama model; read-only answers show every exact path-and-line excerpt and are never stored or applied | W2 | `tests/test_local_chat.py`, `tests/test_local_chat_gui.py`, and `tests/test_privacy.py` |
| W-012 | Document editor | Done | Documents creates or imports local text, edits title/body, prompts on unsaved work, exports explicitly, and rejects stale saves | W2 | `tests/test_documents.py` and `tests/test_document_gui.py` |
| W-013 | Browser extension | Partial | A no-host-permission MV3 extension and network-blocked packaged native host implement hash-bound review, one-use approval, a separate memory-only Echo-Link copy/paste buffer, direct field insertion, password blocking, and exact-ID current-user Chrome/Edge registration plans. The Windows clipboard is never read or written; live browser/register/store proof remains | B1 | `Manuscript/HTML/BrowserBridge`, tests, packaged-host probe, JavaScript syntax checks, `docs/qa/browser-foundation-evidence.json`, and future installed-browser acceptance |
| W-014 | Windows desktop integration | Done | Current application exists | C0 | Windows suite |
| W-015 | macOS desktop integration | Partial | Native SwiftUI, on-device speech boundary, status HUD, guarded Accessibility insertion, and a strict v1 wire binding exist in source; build, native contract operation execution, and live integration remain | M1 | Apple structural validation and future macOS suite |
| W-016 | Mobile integration | Partial | Android builds with strict app-owned on-device speech, system-owned first-run offline pack setup, deterministic review cleanup, encrypted local vocabulary/snippets, explicit reviewed-text sharing, guided keyboard onboarding, and private tap-only input-method insertion; API 36 emulator acceptance passes. iPhone structurally validates with app-owned on-device speech, deterministic cleanup, raw/reviewed separation, and a no-Open-Access keyboard/share flow. Physical-device behavior, signing, provisioning, and store delivery remain | A1/I1 | Android offline build/tests/lint plus emulator evidence, Apple structural validation, and future physical-device suites |
| W-017 | Personal dictionary | Done | SQLite vocabulary | C0 | Storage tests |
| W-018 | Personal learning | Partial | Dictation Review can create explicitly approved local phrase rules with enable/delete/reset controls; broader writing-goal and rejection learning remain | W2 | Learning, reset, privacy, and future writing-quality tests |
| W-019 | Writing profiles | Partial | General, email, meeting, marketing, technical, and code-safe local prompt profiles exist; broader output-quality goldens remain | W2 | Prompt and protected-code tests plus future human-rated profile set |
| W-020 | History and revision | Done | Dictation history remains optional/off by default; document saves create append-only local revisions, preview is read-only, and restore appends a new revision with provenance | W2 | `tests/test_documents.py`, `tests/test_document_gui.py`, and SQLite immutability triggers |
| W-021 | Local data protection | Partial | Windows database/WAL pages use SQLCipher with a random current-user-DPAPI-wrapped key; verified plaintext migration, fail-closed recovery, active-key rotation during purge, schema inventory, guarded imports/exports, strict loopback transport, private clipboard staging, ACL hardening, and opt-in encrypted 15-day speech logs exist. Secure erase, old plaintext blocks/backups, administrators, same-user malware/live memory, target retention, third-party SQLCipher supply-chain assurance, and filesystem TOCTOU remain | W2 | `docs/SECURITY_THREAT_MODEL.md`, `docs/qa/encrypted-storage-evidence.json`, `docs/qa/security-hardening-evidence.json`, encryption/data-protection/transport/clipboard/purge/ACL/privacy/speech-log tests, and future clean-machine recovery acceptance |
| W-022 | Local model management | Done | Echo-Link lists local Ollama tags, versions, digests, sizes, families, and quantization; validates and hashes user-selected GGUF imports; confirms removals; and runs a bounded local benchmark. The release machine passed the repeatable live evidence probe. | W1/W2 | `tests/test_writing_models.py`, `tests/test_writing_models_dialog.py`, `tests/test_writing_model_probe.py`, and `docs/qa/writing-model-evidence.json` |
| W-023 | Quality scoring | Partial | In-app readability scoring and a locked 15-case offline scorer cover grammar/punctuation, style/tone, meaning preservation, summary, and translation checks; the configured local model currently passes 5/15 strict cases, so this is measured fixed-case acceptance, not a general quality claim | W2 | `benchmarks/writing_quality_v1.json`, writing-quality tests, `evidence/writing-quality-live.json`, and human review |
| W-024 | Local team policies | Planned | No policy packs yet | E1 | Policy enforcement tests |
| W-025 | Local administration and audit | Planned | No admin surface yet | E1 | Role and audit tests |
| W-026 | Hosted backend | Excluded | Conflicts with local-only rule | None | Network audit |
| W-027 | Cloud accounts and billing | Excluded | Conflicts with product boundary | None | UI and network audit |
| W-028 | Hosted inference | Excluded | All runtime inference stays on device | None | Network-blocked suite |
| W-029 | Paid API dependency | Excluded | Runtime must work without keys or paid calls | None | Clean-machine offline suite |
| W-030 | Cloud collaboration and sync | Excluded | Use explicit local files and local-network packs if later approved | None | Data-flow audit |
| W-031 | Inline suggestion cards | Partial | A focus-safe Writing HUD automatically detects one stable, visible Windows-accessibility text selection without the clipboard, appears beside it without stealing focus, and shows categorized Spelling, Grammar, Formatting, and Clarity suggestions with before/after detail, filters, Apply one/Apply all, editable preview, guarded Replace, private staging, and Reject. Exact underlines inside third-party rich editors remain | W2 | UI Automation reader/monitor, suggestion-engine, HUD GUI, automatic Notepad focus/clipboard probe, 100-200% scale, stale-selection, accept/reject, and future rich-editor placement tests |
| W-032 | Floating rewrite menu and quick actions | Done | A stable accessible selection opens the compact floating palette automatically; `Ctrl+Shift+Space` remains the manual fallback. Proofread, Rewrite, Shorten, Expand, Summarize, Formal, Friendly, and Translate never apply without explicit review | W2 | `tests/test_windows_selection.py`, `tests/test_rewrite_palette.py`, automatic/manual selection capture, guarded replacement, and accessibility tests |
| W-033 | Document analytics and score dashboard | Done | The Documents Analytics view calculates local readability, word/sentence/paragraph counts, reading time, long sentences, filler/weak wording, and concrete guidance for the active draft | W2 | `tests/test_document_analytics.py` and `tests/test_document_analytics_gui.py` |
| W-034 | Browser Google Docs, Gmail, and rich-text synchronization | Planned | Basic input, textarea, and contenteditable insertion exists; app-specific rich-editor adapters do not | B1 | Installed-browser Docs/Gmail/rich-text acceptance |
| W-035 | Local retention policies | Partial | Typed purge and history-off behavior exist. Encrypted speech improvement logs are off by default, user-visible, individually/delete-all removable, and automatically expire after a configurable 1–15 days. Automatic expiry for other retained content remains open | W2/E1 | Speech-log retention/startup/privacy/UI tests plus future document/history retention controls |
| W-036 | Local embeddings and reranking | Planned | Approved-source search is lexical only; optional local semantic retrieval and reranking remain | W2 | Offline model, ranking, citation-preservation, and network-block tests |
| W-037 | Safety validation model | Partial | Deterministic protected-token, number, meaning, language, and length guards exist; no separate local safety model is shipped | W2 | Guard corpus, refusal handling, and optional local-model benchmark |
| W-038 | Metadata-only audit logging | Planned | Product logs exist, but there is no bounded structured local audit-event store or report | E1 | Retention, redaction, export, purge, and privacy tests |
| W-039 | Sidebar writing assistant | Partial | The active document now has a context-aware Assistant view with all eight commands, profiles, editable review, apply-to-unsaved-draft, private staging, stale-request guards, and no automatic save/apply. A simultaneous side-by-side editor layout and source citations remain | W2 | `tests/test_document_assistant_gui.py`, context boundary, no-auto-apply, citation, and compact GUI tests |
| W-040 | Local organization data and roles | Planned | No encrypted local organization/profile or role model exists | E1 | Role, policy, migration, encryption, and purge tests |
| W-041 | Local draft generation | Done | The visible Drafts workspace creates Email, Meeting summary, Marketing copy, Technical documentation, and Code explanation drafts from user facts through loopback-only local models. Number/claim and protected-term guards fail closed; save and private paste require explicit review | W2 | `tests/test_drafting.py`, `tests/test_drafts_panel.py`, privacy tests, and compact/scaling GUI tests |
| W-042 | First-run writing engine setup | Removed | The optional setup popup and automatic installer were removed. Local writing tools remain available inside the app without a first-run prompt | W1/W2 | Startup smoke test and packaged first-run verification |

## Specification cleanup decision

The AI platform document contains generic repeated entries named `Feature 1` through `Feature 120`. They do not define 120 separate behaviors. This matrix maps the document's meaningful named capabilities and does not create fake feature tickets from those placeholders.

## Immediate failing or unverified proof points

- Live user baseline is not measured.
- The guided 20-case live baseline is not yet completed. Its resumable UI and export path are available only through the separate `--internal-evaluation` launcher, not consumer navigation.
- Automatic lists pass locked and expanded local cases. Natural backtracking passes those text cases but still needs broader real-speech coverage.
- Protected URL, email, path, time, filename, number, and code cases pass the locked corpus.
- Stable-prefix streaming, final-text checkpoints, a 12-stage deterministic failure matrix, and a 9-stage subprocess crash/relaunch matrix are built. Live latency and multilingual hardware coverage remain unverified. Android has a compile-tested app-owned on-device speech/input-method flow; macOS and iPhone have structurally validated app-owned on-device speech sources. Apple compilation, mobile signing/provisioning, and every physical-device proof remain open.
- Local insertion passes single-line, plain-text, rich-text, Notepad, Word, Chrome, VS Code, a chat-like multiline control, and a real Windows terminal. Real third-party chat and elevated-window acceptance remain.
- Executable and installer signing remain unverified.
