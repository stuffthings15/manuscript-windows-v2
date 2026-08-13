# Architecture map

Written so that a change can be made without reading 19,000 lines first. Read
`CLAUDE.md` for the rules; this file is where things live and which module owns
which decision.

Two packages under `src/`:

- `echo_link_core` - all domain logic. No Qt imports.
- `echo_link_app` - the Qt desktop application. No correction logic of its own.

That separation is the one architectural invariant worth defending. When a
business rule appears inside a widget, it belongs in core.

## Dictation path

```
audio.py -> transcription.py -> correction.py -> insertion.py
                    engine.py orchestrates all four
```

| Module | Owns |
|---|---|
| `audio.py` | microphone capture, the ring buffer, device changes |
| `transcription.py` | faster-whisper, full and partial (live preview) transcription |
| `correction.py` | deterministic dictation cleanup: punctuation, casing, filler and repetition removal, backtracking, lists, custom vocabulary |
| `developer_text.py` | Developer mode spoken code, filenames, CLI text. Never calls a writing model |
| `insertion.py` | putting text into the captured target: Auto, private Clipboard, Direct Unicode |
| `windows_selection.py` | UI Automation and Word COM. Finding the target, stable ranges, the non-mutating underline |
| `engine.py` | session state, threading, the live-preview loop, hotkey-driven start and stop |

Live transcription holds `_transcription_lock` around every `transcribe_partial`
call. Full-buffer re-transcription under a shared lock was the cause of the
20-second dictation delays; the back-off in `engine.py` exists because of it.

## Writing and correction path

```
window.py (HUD) -> polish.py -> selected_text.py -> local model
                        |
                        +-> polish_spelling.py, writing_suggestions.py (deterministic)
```

| Module | Owns |
|---|---|
| `polish.py` | `PolishEngine` (orchestration, retries, cascade), `PolishValidator`, the meaning guards, tone and style rewriting, `PolishWritingAssistantAdapter` |
| `polish_entities.py` | which capitalised words are names: document frequency, capitalised misspellings, opaque immutable tokens |
| `polish_proofread.py` | the deterministic proofread pass, one table of rule repairs applied to a finished answer |
| `selected_text.py` | `LocalWritingAssistant`: the local model client, prompt construction, candidate validation and repair, immutable-token restoration |
| `writing_suggestions.py` | the pure, no-I/O deterministic suggestion layer. **Must not** import the dictionary or warm the speller |
| `polish_spelling.py` | the single source of truth for every non-dictionary spelling correction |
| `polish_quality.py` | the tone contract and the correction quality gate |
| `polish_hardware.py` | hardware detection and the `ExecutionPlan` (context window, output tokens, GPU requirement) |
| `polish_types.py` | `CorrectionRequest`, `ExecutionPlan`, `PolishTask` and the shared dataclasses |
| `polish_runtime.py`, `polish_runtime_packs.py`, `polish_model_packs.py` | the bundled llama.cpp host, signed model packs, integrity checks |
| `polish_classifier.py`, `polish_error_score.py` | task selection and error scoring |
| `meaning_diff.py` | what the model changed, and whether the change lost content |

**The `polish_*` convention.** New correction modules join this family. A feature
does not get its own parallel structure.

### Decisions and where they are made

| Question | Answered by |
|---|---|
| Is this word misspelled, and to what? | `polish_spelling.py`, one lookup, layered by specificity |
| How many tokens, how much context, GPU or CPU? | the `ExecutionPlan` from `polish_hardware.py`. The app layer does not get a vote |
| Did the model change the meaning? | the meaning guards in `polish.py` |
| Is this capitalised word a name or an ordinary word? | `_named_entities` in `polish_entities.py`, using document-wide frequency |
| Is this correction good enough to show the user? | `CorrectionQualityGate` in `polish_quality.py` |
| Is the review ready to apply? | the readiness signal exposed by the HUD in `rewrite_palette.py` |

## Storage and privacy

| Module | Owns |
|---|---|
| `storage.py` | SQLCipher-backed local history, documents, revisions, vocabulary |
| `database_crypto.py`, `data_protection.py`, `data_folder_acl.py` | key handling, rotation, at-rest protection |
| `private_clipboard.py`, `private_text_buffer.py` | the memory-only paste buffer that never touches the Windows clipboard |
| `shared_contract.py` | the frozen local-core v1 JSON contract shared with Android and Swift. Version it, do not quietly change its shape |

## Application layer

| Module | Owns |
|---|---|
| `window.py` | the main window, the section rail, the HUD job queue and AI worker loop |
| `rewrite_palette.py` | the Writing HUD: badge, card, review state machine, readiness signal |
| `documents_panel.py`, `drafts_panel.py`, `settings_dialog.py` | their own screens |
| `echo_bar.py`, `theme.py`, `sound_cues.py`, `accessibility.py` | chrome and presentation |

## Debt register

Recorded rather than accumulated silently. Each entry is a known cost, not a
surprise waiting for the next reader.

| Debt | Impact | Remediation |
|---|---|---|
| 149 of the 512 proofread rules match one benchmark sentence rather than a grammar defect | a green test on one of them proves only that the sentence is memorized, and a new essay with the same defect gets no correction | **Accepted as permanent debt, decision taken 2026-08-12.** Two general rules replaced 13. The rest resist generalization at this layer: telling `customer names gate codes and service notes` from a noun phrase needs part-of-speech knowledge, `must not be presented them as` is shaped exactly like the correct `she was shown this as an example`, and a rule for `did not measure analyzed` destroyed `we can use measured data` when tried. `tests/test_source_discipline.py` now freezes the count at 149, so the table can shrink but never grow. Lowering it needs a real rule, an adversarial test, and the ceiling lowered in the same commit |
| `EchoLinkWindow` is 4,017 lines and `RewritePalette` 2,616 | the two remaining oversized units are single Qt classes | four splits done: `polish_proofread`, `polish_entities`, `review_spans`, `polish_readiness`. What is left is not a move but a design change - the HUD job queue and AI worker loop are domain logic inside a `QMainWindow` and need an owner object, not a new file. Attempt it with a clear run and a green suite, not at the end of a long session |
| 137 broad `except Exception` in `src`, of which 88 log or re-raise and 5 swallow silently | a swallowed error hides a real defect | audited. The five silent ones were each read and justified, and `tests/test_source_discipline.py` now fails if a sixth appears. `BLE001` stays ignored in `pyproject.toml` so a non-zero ruff exit means a new defect rather than this backlog |
| The review stops advancing after the first Replace on some documents | essays 7, 12 and 14 of the 2026-08-12 batch each applied one section then froze at `1 of 10, all sections ready`, leaving nine paragraphs uncorrected and 90 to 105 unknown words | not the stale capture, which is fixed: these ran in a single pass with no restart. The harness captured the HUD control state at the moment of failure in `build/corrected/*.review.md`. Worth three essays, and the highest-value defect left |
| `length_ok` in `tools/full_page_tone_quality_probe.py` is computed and never recorded | the probe's length check silently does nothing | decide what it should assert, then wire it into the case record |
| ~~The HUD can stay bound to a Word window the writer has moved on from~~ **fixed 2026-08-12** | it killed essays 5, 6 and 7 of the 2026-08-11 batch: every Replace refused, then a fresh capture demanded, and the counter reading 0 of 0 | `_hud_review_still_in_focus` now compares the focused window with the review's source window, so a review no longer swallows focus signals from a different document. Verified in the live batch: all 20 essays ran in a single pass, no restarts |
| Text-to-speech playback speed | reported slightly slow, never investigated | unmeasured |
| Flaky: crash recovery, key rotation | intermittent suite failures, possibly disk-space related | unreproduced |
| One dropped content word (`looking`) and roughly 12 stray unknown words in corpus output | small quality loss on the essay corpus | tracked by `meaning_diff.unexplained_losses` |

## Verification

```bash
.venv/Scripts/python.exe -m pytest -q
```

Baseline 2032 passed, 2 skipped.

```bash
.venv/Scripts/python.exe -m ruff check src tests tools
```

Must exit clean. The selected rules are narrow on purpose (`F`, `BLE`, `T20`),
so a finding is a real defect rather than a style opinion.

Entity behaviour that moved the essay corpus from 7 to 14 complete is guarded by
`tools/entity_frequency_probe.py` and `tools/dead_essay_probe.py`.

End-to-end correction quality is measured by running the 20-essay corpus through
the HUD in real Word with `tools/run_essays_batch.py`. Last measured, 2026-08-12:
17 complete, 3 partial, 0 dead, 10 perfectly clean. That run takes the screen for
about 45 minutes, so ask first.
