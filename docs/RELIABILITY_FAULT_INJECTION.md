# Reliability Fault-Injection Proof

Date: July 19, 2026

Run the local deterministic matrix:

```powershell
.\.venv\Scripts\python.exe tools\reliability_fault_probe.py
```

The probe uses only fakes, memory audio, temporary SQLite databases, and temporary folders. It does not open a microphone, contact a service, or write audio.

## Covered stages

| Stage | Required result |
|---|---|
| Microphone start failure | Cancel partial capture, clear hands-free state, report `error`, save no recovery text |
| Microphone stop failure | Cancel capture, stop all workers, mark an existing stable checkpoint `failed` |
| Microphone cancel failure | Cancel capture and discard the canceled checkpoint even when stop fails |
| Transcription exception | Report `error`, insert nothing, preserve only the prior stable checkpoint |
| Empty transcription | Report `error`, insert nothing, create no empty recovery item |
| Deterministic correction exception | Report `error`, insert nothing, preserve only the prior stable checkpoint |
| Accuracy refinement exception | Fall back to deterministic text, insert once, warn, complete, clear recovery |
| Refine-mode exception | Keep the already inserted deterministic text, warn, complete, clear recovery |
| Insertion exception | Report `error` and mark the final unsent text `failed` |
| Recovery checkpoint write failure | Complete dictation, insert once, and warn that recovery was unavailable |
| Recovery status write failure | Still reach and emit terminal `error`; do not let the worker escape |
| Recovery cleanup failure | Keep successful insertion and `complete`, retry cleanup, expose a warning |

Every stage also checks these invariants:

- Recorder, dictation worker, live preview, silence watchdog, and recording timer stop.
- History remains empty.
- No `.wav`, `.raw`, `.pcm`, or other audio file appears anywhere in the stage folder.
- Failed insertion never gets retried blindly.
- A successful insert is not mislabeled as a failed dictation when optional refinement or recovery cleanup fails.

## Current result

- 12 of 12 injected stages passed.
- `local_only` is `true`.
- `raw_audio_retained` is `false`.
- A separate 9-stage subprocess matrix passed at recording, transcribing, deterministic correction, after the final checkpoint, Accuracy refinement, insertion, recovery cleanup, post-insert refinement, and normal completion.
- Every crash-stage relaunch found the expected stable/final text or no row, found no audio, and did not change the external-insertion marker.
- Insertion and cleanup crashes persist `insertion_uncertain` with an explicit check-the-target/no-auto-retry warning. Post-insert refinement crashes leave no duplicate recovery item because the checkpoint is already cleared.
- `scripts\verify.ps1` runs both matrices with the normal suite, privacy checks, and diagnostics.

## Boundary

This proves deterministic Python failure handling and abrupt exit code 17 across the core engine boundaries. Real microphone hardware faults, operating-system or power loss, and clean-machine hardware checks remain separate acceptance work.
