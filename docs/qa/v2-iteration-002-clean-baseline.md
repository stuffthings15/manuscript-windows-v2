# Version 2 Iteration 002: Clean Baseline

Date: 2026-08-04

## Fixed

- Bootstrap now falls back when the Windows Python launcher returns a failure code and verifies that the virtual-environment interpreter exists.
- Version 2 identity expectations now match its isolated display name and product namespace.
- The compact header remains readable at all four supported display scales.
- Proofread validation rejects a changed requirement when AI turns `must` into permission such as `may`.
- Automated UI tests cannot start model discovery, model warm-up, or unowned HUD AI jobs. This prevents late model calls from contaminating later tests or occupying GPU memory.
- Runtime status watcher errors remain inside the daemon boundary and stop after HUD shutdown.
- The locked 20-essay probe now requires the exact local corpus hash, all 20 essays, 10 ordered AI-reviewed sections per essay, no Skip, no rules-only review, and no truncated result.

## Red Evidence

The first clean-environment full run reported:

```text
11 failed, 1640 passed, 2 skipped
```

After the initial repairs, two full runs each exposed a separate late HUD worker changing the mocked local-AI call sequence. Focused red tests reproduced both missing shutdown switches before implementation.

## Green Verification

Focused background-worker and correction retry gate:

```text
4 passed in 1.67s
```

Complete suite:

```text
1661 passed, 2 skipped in 268.85s
```

The two skips are explicit gates: symbolic-link support is unavailable on this machine, and live external-insertion evidence has not yet been recorded.

Packaging recipe:

```text
python tools/build.py --dry-run --testing-unlocked
```

Result: passed. It selected the Version 2 native host, Browser Host, PyInstaller specification, and testing-unlocked channel.

## Remaining Release Blocks

- A slow or incorrect model acceptance probe is still allowed to pass and must become a hard downgrade gate.
- Current hardware scaling is not Gemma-only and several features bypass the shared hardware policy.
- The visible HUD can still expose a rules-only result after AI failure.
- Proven tray restore, nonblocking dictation, stable Word page capture, and exact Word range replacement repairs still need to be ported and retested.
- The bundled llama.cpp host executable and signed runtime packs are not present in the V2 package.
- The 20-essay visible run, independent GPT-5.6 Sol review, physical Word test, packaged stress test, subscription lifecycle, signing, and sales-release audit remain blocked.
