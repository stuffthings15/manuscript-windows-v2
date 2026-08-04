# Echo-Link Product Roadmap

## Locked rules

- All speech, writing assistance, personalization, and user data stay on the device.
- No hosted inference, cloud account, cloud sync, paid API, or required network connection.
- GPT supports planning, requirements, review, and capstone evidence only. GPT is not part of the Echo-Link runtime.
- Product targets: Windows, macOS, iPhone, and Android.
- Capstone target: one narrow, measured Windows proof before the wider product.
- The human user reviews every capstone output before it counts as accepted.

## Current specification delivery plan

| Priority | Scope from the two product specifications | Current state | Exit gate |
|---|---|---|---|
| 1 | Windows dictation: global hotkey, Echo Bar microphone state, start/stop cues, local transcription, safe insertion, undo, and private paste that never replaces the Windows clipboard | Implemented | Repeat source and packaged workflow probes on the release build; keep real Notepad/Word/browser/chat/IDE and elevated-target checks in the release record |
| 2 | Visible writing suite: automatically appearing categorized local Writing HUD, eight selected-text tools, five draft types, document editor/revisions, document analytics, and active-document assistant | Implemented | Full automated suite, automatic focus/clipboard probe, compact and 100-200% layout checks, live loopback-model quality review, stale-selection refusal, and no-auto-apply proof |
| 3 | First-run readiness: bundled core dictation plus optional reviewed download of the local writing engine/model | Implemented | Clean-machine install, consent, Skip, interrupted-download, retry, and offline-after-setup checks |
| 4 | Personal writing goals, saved style preferences, deeper accept/reject learning, true selection-anchored rich-editor cards, and side-by-side document assistance | Next Windows work | Local schema/privacy tests, reset/export controls, human quality set, and installed-app accessibility proof |
| 5 | Native macOS, iPhone, and Android parity | Foundations implemented | Physical devices, native builds, permissions, keyboard/IME insertion, accessibility, signing, and store packages |
| 6 | Browser rich-editor adapters and optional local organization tools | Later local-only release | Google Docs/Gmail rich-text acceptance, encrypted local roles/policies/audit retention, and no hosted service |

## Capstone proof: three weeks

### Must have

1. Hold the hotkey, speak, release, process locally, review, and insert or copy polished text on Windows.
2. Run a fixed 20-case evaluation and show the difference between the untouched baseline and the improved build.
3. Record correction-free acceptance and time to accepted text in a live user session.
4. Preserve names, numbers, URLs, email addresses, paths, filenames, and code tokens.
5. Work with history off, raw audio deleted, and the network blocked.
6. Show a focus-safe Echo Bar and play local start/stop cues for every hotkey session.

### Should have

- Better natural backtracking. Implemented for the fixed and expanded local regression sets; live speech variants remain.
- Automatic lists. Repeated-line, spoken-number, ordinal, and explicit bullet forms are implemented.
- Snippets and protected-token controls.
- Recovery after a failed transcription or insertion.
- A review view with raw text, corrected text, differences, Accept, Insert, Copy, Retry, and Reject.

### Could have

- Whisper mode.
- More local writing styles.

### Won't have in the capstone

- macOS, iPhone, or Android delivery.
- Browser extension.
- Team administration or enterprise reports.
- Cloud features of any kind.
- Online plagiarism lookup.
- Hosted AI or paid API calls.

### Week 1: freeze and measure

- Create a local Git baseline.
- Restore a Python 3.12 environment with pinned direct dependencies.
- Run the full automated suite.
- Lock the 20-case corpus and scoring rules.
- Run the automated text baseline.
- Keep capstone evaluation outside normal product navigation. The separate `--internal-evaluation` surface is implemented; use it if the human baseline resumes, then set numeric targets from its local results. Do not restore a Capstone tab link to the consumer app.
- Maintain the requirements traceability matrix.

### Week 2: build the proof

- Add Review mode.
- Improve backtracking, fillers, punctuation, lists, snippets, and protected tokens.
- Add local accepted/rejected feedback records without storing raw audio. Implemented, with optional user-approved short phrase learning that never stores the full dictation.
- Add retry plus an opt-in durable failed-text recovery queue with no retained audio. Implemented, with a 12-stage deterministic failure matrix and a 9-stage subprocess crash/relaunch matrix.
- Keep Fast mode available as the low-latency path.

### Week 3: prove and package

- Repeat the locked corpus without changing expected outputs.
- Compare baseline and final results.
- Test Notepad, Word, browsers, chat, and an IDE.
- Test with history off and the network blocked.
- Build the executable and installer.
- Same-machine current-user install, launch, repair, uninstall, private-data preservation, reinstall, and cleanup pass 21/21 with content-free evidence. Repeat on a clean Windows machine and add versioned upgrade/rollback proof.
- Finish the one-page proposal, result table, known risks, demo script, and traceability matrix.

## Product releases after capstone

### Release 1: Windows voice hardening

- Streaming partial transcription and stable final segments. Stable-prefix preview is implemented; live latency work remains.
- System tray lifecycle, hands-free toggle with Esc cancel and silence auto-stop, memory-only pre-roll, safe recording limit, bundled synthesized activation/completion cues, selectable local WAV cues, output routing, Auto Bluetooth wake delay, owned media pause/resume, and draggable saved Echo Bar placement are implemented. Physical Bluetooth coverage remains.
- Multilingual local models. Installed-model recommendation, one-time first-run apply, local speed check, and guided offline model-pack import/removal are implemented.
- Automatic lists, snippets, style profiles, approved learned corrections, and deterministic Developer mode are implemented. Developer mode covers built-in and user vocabulary, explicit identifier case, spoken filenames and `@file` tags, syntax symbols, indentation, and CLI text without executing it or calling a writing model.
- Recovery queue, safe insertion, undo, and a tested application matrix. Echo-Link now has a separate process-memory copy/paste buffer with `Ctrl+Alt+V`, clipboard-free Direct insertion, explicit clear/status, and Direct as the new-profile default. Auto/Clipboard compatibility modes remain explicit and guarded by Windows history/cloud exclusion plus restoration. Partial-send protection, the repeatable workflow probe, live Word, external textbox, chat-like multiline, and real Windows terminal passes are implemented; elevated-app, real third-party chat, and broader real-app coverage remain.
- The desktop interface now uses a persistent nine-section left rail, global microphone/hotkey/private-paste status, stronger high-contrast boundaries, responsive Dictate controls, context-only review actions, category-based Settings pages, and a larger focus-safe Echo Bar. Automated compact/desktop layout and 100-200% scaling checks pass; physical display and assistive-technology acceptance remains.

### Release 2: local writing suite

- Grammar, spelling, punctuation, style, tone, and readability. A locked 15-case offline scoring corpus now covers these categories plus meaning, summary, and translation checks; real local-model and human-rated quality evidence remains.
- Deterministic local readability score and feedback. Implemented.
- Rewrite, shorten, expand, summarize, and translate with local models.
- The main Writing tools page exposes Proofread, Rewrite, Shorten, Expand, Summarize, Formal, Friendly, and Translate. The selected-text hotkey opens the same commands in a compact review palette with explicit Replace, Stage private paste, and Reject actions.
- Email, meeting, marketing, technical, and code-safe profiles are implemented. The Drafts workspace also generates five explicit local draft types: Email, Meeting summary, Marketing copy, Technical documentation, and Code explanation.
- Documents now include local analytics and an active-document assistant. Both use the current unsaved draft, never auto-save, and require explicit Apply, Save, or private-paste staging.
- Core dictation is bundled. The first-run local writing setup popup and automatic installer were removed; writing tools remain available inside the app.
- Local writing-model management is implemented: installed-model metadata, validated and hashed GGUF import, confirmed removal, selection, and a bounded release-machine benchmark.
- Local chat over user-approved local `.txt` and `.md` documents. Implemented with a session-only index, loopback-only Ollama, read-only answers, and exact path-and-line excerpts.
- Local document editor with explicit import/export, unsaved-change and stale-revision guards, append-only revisions, preview, and restore-as-new-revision. Implemented.
- Local data inventory, schema guard, guarded imports/exports, redaction manifests, comprehensive local path controls, strict loopback model transport, clipboard-history/cloud exclusion, SQLCipher database/WAL encryption, current-user DPAPI key wrapping, verified plaintext migration, active-key rotation during typed purge, and explicit Windows ACL hardening are implemented. W-021 stays Partial because secure erase, old plaintext blocks/backups, administrators, same-user malware, live memory, external targets, third-party SQLCipher supply-chain assurance, and filesystem TOCTOU remain open.
- Citations for user-approved local `.txt` and `.md` sources. Implemented with a memory-only index.
- Plagiarism comparison against a user-selected local corpus only. Implemented with no online lookup.

### Release 3: shared native core

- Freeze a versioned local contract for stable speech, correction, protected-token, writing-command, privacy, capability, and error behavior. The v1 schemas, 10 exact goldens, Python reference adapter, strict Android v1 binding, and strict Swift request/result/error binding source with Windows conformance validation are implemented; Swift/Xcode compilation and native operation execution remain.
- Extract stable storage and evaluation rules into the future native `echo_core` implementation behind that contract.
- Keep the current Python/PySide6 Windows app while the native core proves parity.
- Use golden tests to require identical behavior before switching adapters.
- Primary candidates: whisper.cpp for local speech and llama.cpp for optional local writing models.
- Provide Windows, Swift, and Android bindings without adding a network dependency.

### Release 4: macOS

- SwiftUI source foundation with app-owned microphone capture, required on-device Apple speech recognition, and a strict shared-contract wire binding. Implemented in source; Xcode compilation, signing, native operation execution, and device proof remain.
- Accessibility-based focused-app insertion with review, captured external target, editable-role checks, and opt-in guarded clipboard fallback. Implemented in source; live Accessibility acceptance remains.
- Focus-safe status panel and microphone permissions exist in source. Menu bar controls, global shortcuts, production storage, and hardware acceptance remain.

### Release 5: Android

- Native containing app uses Android 12+ app-owned on-device recognition only, with no generic/cloud fallback. Android 13+ checks the system offline speech pack on startup and requests a system-owned download when the current language is supported but missing; the app itself still has no `INTERNET` permission.
- Raw speech and deterministic local cleanup are shown separately before manual review and approval. The high-contrast, inset-safe API 36 interface has stable accessibility IDs, live status regions, 48dp controls, a launcher icon, and visible enable/choose keyboard onboarding.
- The approved payload is encrypted at rest with a non-exportable Android Keystore AES-256 key and AES-GCM, legacy plaintext is purged, storage errors fail closed, and the system input method consumes one payload before a single user-tapped insertion attempt. The keyboard never captures microphone audio or uses the Android clipboard.
- API 36 emulator acceptance passed app restart persistence, system speech-pack setup, active-keyboard status, exact insertion into Android Settings search, and no second insertion. Physical microphone/OEM/Keystore proof, TalkBack/large-font/landscape/RTL checks, release signing, store packaging, and optional on-device writing-model selection remain.

### Release 6: iPhone

- The containing-app source requires Apple's on-device speech recognizer, reports permission/model-unavailable states, shows an editable live/final review draft, and writes one explicit short-lived approved payload only after a separate approval tap. Xcode and physical-device behavior remain unverified.
- The custom-keyboard source requests no Open Access, reads but cannot write the shared payload, never captures audio, and inserts only after a user tap.
- The share-extension source reviews and approves user-selected plain text into the same bounded payload.
- App Group storage, SHA-256 integrity, expiry, size bounds, and private consumed-ID tracking are implemented in source. Xcode/App Group provisioning proof remains.
- Microphone capture is allowed only in containing apps, never promised inside the custom keyboard.

### Release 7: browser and local enterprise

- The browser foundation now includes a no-host-permission MV3 extension, an offline packaged network-blocked native host, exact-extension-ID current-user Chrome/Edge registration scripts with plan-only review, hash-bound approval, and a one-use 120-second Echo-Link private buffer that directly inserts without reading or writing the Windows clipboard. Live unpacked loading, registration, field insertion, restart, signing, clean-machine, and store proof remain.
- Local team dictionaries, snippets, policy packs, audit records, reports, and document indexes.
- No cloud SSO, billing, live cloud dashboard, or cloud collaboration.

## GPT project workflow

- Project instructions contain the locked local-only rules and acceptance tests.
- Reference files contain the capstone, both Echo-Link specifications, roadmap, traceability matrix, corpus, and results.
- Separate GPT chats handle requirements, test review, risk review, and capstone writing.
- Human review accepts or rejects every proposed change and every capstone output.
- Accepted decisions go into code, tests, docs, and AI Brain. Personal dictation content does not go into GPT.

## Release gates

- Unit, integration, GUI, corpus, microphone, cross-app, packaging, and clean-install checks pass.
- Protected content does not regress.
- No raw audio remains after processing unless the user explicitly enables retention.
- History-off mode stores no dictated text.
- Network-blocked use succeeds.
- Idle CPU, GPU, and model activity are measured and within the target set after baseline.
- Accessibility, keyboard control, display scaling, signing, checksums, and rollback are verified before public release.
- Every shipped requirement has a code location, a test, and a traceability status.
- Private storage may be called encrypted only for the active Windows SQLCipher database/WAL boundary verified in the threat model. Do not extend that claim to exports, backups, target applications, secure erase, or protection from administrators and same-user malware.
