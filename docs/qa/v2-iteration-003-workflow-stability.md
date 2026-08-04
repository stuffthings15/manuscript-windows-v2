# Version 2 Iteration 003: Workflow Stability

Date: 2026-08-04

## Fixed

- Dictation capture now runs independently from transcription and AI correction.
- Dictation jobs use a strict FIFO queue with immutable target and settings snapshots.
- A second or third recording can start while earlier dictation is processing.
- Refine mode waits for AI before insertion instead of inserting a rules-only draft first.
- The correction provider now starts a nonblocking AI preload when recording begins.
- The system tray action restores, raises, and foregrounds the main window.
- Microsoft Word capture reads the current page instead of one sentence or the entire document.
- Word review keeps stable page offsets and replaces only the accepted range.
- The review window no longer exposes deterministic text as a finished suggestion when AI fails.
- A failed section can be retried or dismissed while later background sections continue.
- Hardware acceptance now requires correctness, protected-fact preservation, latency, and full GPU residency when the selected profile requires GPU execution.
- A failed or slow model pack is no longer restored as the active manual model.
- The accessibility probe now loads the source package correctly from a clean V2 environment.
- The crash matrix now tests a true post-insertion crash after the recovery checkpoint is cleared.

## Red Evidence

Focused tests first proved these failures:

- AI failure exposed rules-only replacement text.
- A failed acceptance probe could still become the active execution plan.
- Rapid dictation was blocked while the prior job was transcribing or correcting.
- The tray restore path did not call the native Windows restore operation.
- A partial Word page did not have a stable replaceable range.
- The real correction provider had no preload implementation.

The first complete run after merging the repairs found five additional checkpoint failures:

```text
5 failed, 1672 passed, 2 skipped
```

Four were clean-environment accessibility import failures. One was a stale crash-probe assumption from the old post-insert refinement order.

## Green Verification

Merged workflow gate:

```text
431 passed in 216.38s
```

Dictation and real preload gate:

```text
64 passed in 2.98s
```

Accessibility and crash-recovery gate:

```text
23 passed in 10.06s
```

Complete V2 suite:

```text
1677 passed, 2 skipped in 318.52s
```

The two skips remain explicit evidence gates: symbolic links are unavailable on this machine, and live external insertion evidence has not been recorded.

## Remaining Release Blocks

- Convert the signed model catalog and every runtime selection path to official Gemma 4 packs only.
- Rotate the V2 manifest key once and sign both the model-pack and runtime-pack manifests with that same key.
- Run measured local acceptance on each installed Gemma 4 candidate and select the fastest passing pack.
- Run all 20 essays through the visible user workflow for every tone and grade the outputs independently with GPT-5.6 Sol.
- Verify the packaged build in physical Microsoft Word, Notepad, and a browser text field.
- Complete bundled llama.cpp runtime packaging, code signing, subscription lifecycle testing, and the final sales security audit.

This checkpoint is not a public sales release.
