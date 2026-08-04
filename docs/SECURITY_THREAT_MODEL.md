# Echo-Link Local Security Threat Model

Status: reviewed 2026-07-20. W-021 remains **Partial**.

Echo-Link is local-only and now encrypts its Windows database and WAL pages with SQLCipher. A random 256-bit database key is wrapped for the current Windows user with DPAPI. This review also covers guarded exports/imports, local-path checks, clipboard staging controls, strict loopback model transport, a complete logical purge with active-key rotation, explicit Windows data-folder ACL hardening, and a schema inventory. It does **not** claim secure erase or protection from an administrator, same-user malware, live process-memory access, operating-system backup tools, target applications, or already exported files.

## Assets and trust boundaries

- Private text: dictation, snippets, corrections, recovery text, documents, revisions, evaluation text, and approved source content.
- Context: target-window titles, timestamps, modes, paths, errors, model names, and UI settings.
- Raw audio: held in process memory during recognition; when encrypted speech improvement logging is explicitly enabled, actual 16-bit PCM is retained in the SQLCipher database for no more than 15 days.
- Local database boundary: `echo-link.db` and its WAL pages are encrypted by SQLCipher. `echo-link.key.dpapi` contains only the DPAPI-wrapped database key. SQLCipher temporary storage is forced to memory. The current-user, SYSTEM, and local-Administrator ACL boundary is separate from encryption.
- Migration boundary: an existing plaintext SQLite database is checkpointed under the single-app lock, exported into a same-folder encrypted database, compared with a typed schema/value fingerprint, integrity-checked, flushed, and atomically replaced. Failure stops startup without falling back to plaintext.
- Export boundary: a path explicitly selected by the user. Exports must remain on a local, non-linked filesystem destination.
- Private paste boundary: **Stage private paste** stores text only in process memory, clears it at shutdown or on user request, and `Ctrl+Alt+V` inserts through Direct input without reading or writing the Windows clipboard. Direct is the default for new profiles. Explicit Auto/Clipboard compatibility modes can still briefly stage text with Windows history/cloud exclusion formats and restore the prior clipboard when safe. External selected-text capture still needs guarded temporary clipboard access, and target applications can retain inserted text.
- Local model boundary: speech models run in-process. Adaptive Polish prefers a
  current-user-only, randomly authenticated named pipe to the Manuscript Polish
  host. The host launches one SHA-verified llama.cpp backend on authenticated
  numeric loopback with its UI disabled. Startup-only logs prove GPU residency,
  then logging is disabled before writing is sent. Ollama remains a bounded
  numeric-loopback rollback and temporary signed-model download bridge.
- Browser boundary: the optional Chrome/Edge extension sends only an explicit
  editable-field correction request through Chrome Native Messaging to the
  current-user Manuscript browser host. The host accepts bounded framed JSON,
  validates every field, uses the local Polish pipeline, and persists no page
  text. Production requests require a live active entitlement.

## Persisted text inventory (schema v8)

`data_protection.PERSISTED_TEXT_FIELD_POLICY` is the executable text allowlist. The privacy audit fails when a new table, unreviewed `TEXT` column, secret-named column, audio-named column, or `BLOB` column appears, except for the single reviewed `speech_improvement_logs.audio_pcm` encrypted-database field.

| Table | Persisted text | Retention behavior |
|---|---|---|
| `schema_meta` | key, value | App lifetime |
| `settings` | key, settings JSON | App lifetime; includes paths and preferences, not credentials |
| `vocabulary` | spoken/written forms, category, time | Until user removal/reset |
| `dictation_history` | raw/final text, mode, window title, time | Off by default; retained when explicitly enabled |
| `speech_improvement_logs` | raw/cleanup/suggested/final text, context, review, analysis, times; reviewed PCM BLOB | Off by default; explicit opt-in; user-visible/deleteable; automatic expiry after 1–15 days |
| `review_feedback` | decision, mode, time | Metadata only; no transcript/audio columns |
| `learned_corrections` | source/replacement phrases, origin, times | Off by default and explicitly approved |
| `snippets` | spoken trigger, inserted content, time | Explicitly saved; content may itself be sensitive |
| `recovery_items` | final text, mode, error, session key, status, time | Off by default; retained when explicitly enabled |
| `documents` | title, current content, times | Explicitly created/imported; no automatic expiry |
| `document_revisions` | title, content, label, time | Append-only; no automatic expiry |
| `evaluation_runs` | kind, commit, status, times | Explicit capstone work; no automatic expiry |
| `evaluation_trials` | case, decision, final text, notes, times | Explicit capstone work; no automatic expiry |

Operational logs can contain errors, file paths, and device/model names. They are not intended to contain transcript text or raw audio. Approved source chat content, questions, answers, and citations remain memory-only. Startup configuration stores the executable path in the current user's startup settings.

## Secret boundary

Schema v8 has **no API-key, token, password, private-key, or credential field**. Legacy API-key data is intentionally not imported. `PERSISTED_SECRET_FIELDS` is deliberately empty because the database contains user text and optional encrypted speech logs, not application credentials. The SQLCipher key never enters this schema.

If a future feature needs a credential, it must not add a settings/database field. It first needs a product-namespaced operating-system secret backend (Windows Credential Manager/DPAPI, macOS Keychain, or Android Keystore), lifecycle and deletion rules, and negative database/export/log tests.

User-authored text can still contain credentials. Structured exports redact secret-named fields and common token/private-key patterns. Exact document exports preserve the user's chosen document text; they are explicit content exports, so the user remains responsible for secrets intentionally placed in that document.

## Export controls

`LocalExportGuard` provides one bounded, atomic export path:

- rejects UNC, mapped-network, linked, junction, traversal, root, unsupported-extension, and unsafe parent destinations;
- rejects binary values and raw-audio field names;
- redacts secret-named fields and common secret patterns in structured/text reports;
- emits a JSON manifest containing the product ID, export type, included sections, content flags, redaction record, byte count, and SHA-256 payload hash;
- uses same-directory temporary files and atomic replacement/linking so a failed overwrite leaves the previous file intact.

Capstone JSON exports embed this manifest. Capstone CSV uses the same sanitized data and guarded atomic writer. Document `.txt`/`.md` export uses the guarded writer but preserves exact user text; its manifest is returned internally rather than adding unexpected text or a sidecar to the document.

Database/app-data locations, model overrides, model-pack sources and destinations, custom WAV files, GGUF imports, approved sources, plagiarism re-checks, legacy database imports, and workflow/evidence outputs now use the same local non-link boundary checks. These checks reduce accidental remote persistence; they are not a defense against an administrator changing mounts or filesystem state after validation.

## Threats, controls, and remaining risk

| Threat | Current control | Remaining risk |
|---|---|---|
| Hosted service receives text/audio | Hosted AI endpoints/imports are blocked. The preferred writing path uses a current-user-only authenticated named pipe and an authenticated numeric-loopback llama.cpp child. Ollama rollback stays on bounded numeric loopback. The only approved remote app module sends a license key and random installation hash to the fixed HTTPS entitlement origin | A compromised same-user llama.cpp/Ollama process can read text sent to it; the entitlement service necessarily sees pseudonymous license and installation metadata |
| Runtime/model package is replaced | RSA-3072 signed allowlists pin exact archive/model sizes and SHA-256 digests; local roots reject network paths, traversal, links, and junctions; ZIP extraction rejects links, traversal, expansion bombs, and conflicts; installed files are re-hashed before launch | Same-user malware or an administrator can alter the app and its trust code; trusted Authenticode signing and clean-machine tests remain required |
| Another process connects to the Polish host | Pipe ACL is current-user-only, pipe names are random, every request uses a random 256-bit secret with fixed-time comparison, messages are bounded, and the host exits with its parent | Same-user malware can inspect live process memory or race local resources; this boundary does not claim defense against the same user or an administrator |
| A web page abuses the browser correction bridge | The extension has no host permissions, handles only editable controls, rejects passwords/read-only fields, bounds request and response sizes, and sends through a fixed Native Messaging host. The native manifest allowlists the packaged extension ID. Every result is review-only before replacement | The extension content script necessarily reads the active field selected by the user; a compromised browser profile, extension, native-host manifest, or same-user process can observe or alter live text |
| Browser correction destroys rich formatting | Plain inputs use range replacement; rich contenteditable documents remain review-only unless a stable selected text range can be replaced safely. No whole-document generative replacement is automatic | Browser editors vary, so live application-specific acceptance remains required |
| Subscription check is bypassed | Frozen production startup gates the main window on a live server entitlement and rechecks every 15 minutes; local state is DPAPI-protected and the server queries the fixed PayPal subscription API, requires the configured plan, and accepts only `ACTIVE` status | A determined attacker can patch a desktop binary; code signing and server-side monitoring reduce but cannot remove this risk |
| License key is stolen or shared | D1 stores only a license hash; the local key is DPAPI-protected; installation counts are enforced server-side | A key exposed from the activation screen or running process can be abused until reset; edge rate limits and support reset tooling remain required |
| Forged or reused PayPal subscription ID grants access | Activation performs a live PayPal subscription lookup, requires an exact configured-plan match, accepts only `ACTIVE`, and binds the PayPal account email to one Manuscript account | PayPal/D1 outages can block checks; exposed IDs and the full billing lifecycle still require live sandbox testing and monitoring |
| Raw audio retained unexpectedly | Speech logging is off by default; the sole reviewed PCM field is in SQLCipher, has a 15-day hard cap, is hidden after expiry, is purged at startup/use/hourly while running, is available in History, and is included in private-data purge; exports reject audio fields and binary values | Audio exists in live memory while used; ciphertext plus the current user's wrapped key may remain usable in backups; filesystem secure erase is not claimed |
| History retained without consent | History and recovery text default off; engine privacy test verifies a successful dictation creates no history | Documents, snippets, evaluations, review metadata, and opt-in recovery are separate stores |
| Private export reaches remote/link target | Local path guard denies UNC, mapped drives, links, junctions, and traversal | TOCTOU and privileged mount manipulation remain possible |
| Secret leaks in report | Structured/text report redaction plus manifest | Free-form redaction is heuristic; explicit document export preserves exact content |
| Schema silently gains private fields | Exact text allowlist and rejection of unknown tables, secret/audio names, and BLOBs except the single reviewed encrypted PCM field | A newly approved field is encrypted at rest but can still increase retention and exposure while the app is running |
| Database read from copied files | SQLCipher encrypts database/WAL pages; a random 256-bit key is DPAPI-wrapped for the current user; standard SQLite and a wrong key fail | Administrators or same-user malware can access the live process/user context; backups containing ciphertext plus the wrapped key may remain usable by that Windows profile |
| Plaintext database upgrade loses data | Startup lock, WAL checkpoint, source integrity check, `sqlcipher_export`, typed schema/value fingerprint, cipher integrity check, and atomic replacement | Existing plaintext disk blocks, backups, or snapshots can remain after migration |
| Echo-Link copy/paste overwrites the Windows clipboard | Separate process-memory buffer, explicit clear/status, dedicated `Ctrl+Alt+V`, Direct-only private paste, and Direct default for new profiles; private paste has no clipboard fallback | Explicit compatibility modes and selected-text capture still use guarded temporary clipboard access; target apps and compromised local software can retain/observe text |
| Deleted text remains recoverable | Typed-confirmation purge clears all private stores, checkpoints/truncates WAL, vacuums when possible, rotates the active SQLCipher key, proves the old key no longer opens the active database, and clears bounded product logs | Filesystem remnants, old DPAPI blobs, backups, snapshots, exports, and target-app copies may remain; this is not secure erase |
| Database key is lost or corrupted | Key writes and rotations use same-folder atomic replacement plus a DPAPI-wrapped pending key and two-state recovery | Losing the Windows profile/key file makes the encrypted database unrecoverable; Echo-Link has no recovery key |
| Logs/evidence expose context | Product logs are intended to avoid transcripts; several probes use synthetic/hash evidence | Errors, paths, and developer evidence can still expose context and need continued audit |

## Verification claims

Focused tests prove:

- history, recovery text, and speech improvement logging are off by default;
- the normal offline dictation privacy test retains no history;
- only the exact reviewed encrypted PCM database field is accepted by the schema audit; all audio and binary exports remain blocked;
- export secret redaction and manifest flags work;
- UNC, simulated mapped-drive, and linked-parent destinations fail closed;
- document overwrite remains atomic;
- current schema v8 exactly matches the reviewed text and encrypted-audio inventory;
- speech logs retain actual PCM and transcript stages only when enabled, cap expiry at 15 days, purge expired rows, support in-app review/deletion/playback without a decrypted temporary file, and keep plaintext sentinels out of raw database/WAL bytes;
- every runtime writing-model request rejects unsafe hosts/paths before opening a socket, ignores proxies, blocks redirects, and enforces bounded JSON responses;
- private clipboard staging exposes all three Windows history/cloud exclusion formats and restores the previous clipboard in a live synthetic check;
- Echo-Link private copy/paste stages no database value, uses a separate memory buffer, inserts only through Direct mode, leaves the Windows clipboard API untouched, and clears on shutdown/user request;
- typed purge clears the reviewed private stores and performs best-effort SQLite/WAL compaction without claiming secure erase;
- fresh databases do not expose the SQLite plaintext header, standard SQLite and wrong keys fail, and private sentinels are absent from raw database/WAL bytes;
- the database key is random, absent from the DPAPI key file as raw bytes, scoped to the current Windows user, and never requested with machine-wide DPAPI scope;
- closed plaintext databases migrate with schema, triggers, indexes, typed values, and internal sequences preserved before atomic replacement;
- interrupted key rotation safely keeps the old key or promotes the pending key, and purge makes the old key fail against the active database;
- live migration completed on the release machine, SQLCipher reports `4.12.0 community`, the DPAPI key file exists, and the live file no longer has the `SQLite format 3` header;
- Windows ACL hardening verifies the data folder and fixed private files with only the current user, SYSTEM, and local Administrators granted access;
- all remaining named import/export/model/source/evidence path surfaces reject UNC, mapped, linked, junction, traversal, and linked-parent paths.

These tests prove the active Windows database/WAL encryption and key lifecycle described above. They do **not** prove secure deletion, removal of old plaintext blocks/backups, resistance to administrators or same-user malware, live-memory protection, target-app deletion, freedom from filesystem TOCTOU, or physical-device microphone quality. The `sqlcipher3` Windows wheel is a pinned third-party dependency rather than an official Zetetic build and was not uploaded through PyPI Trusted Publishing. W-021 remains Partial because those residual risks and clean-machine upgrade/recovery testing remain open.
