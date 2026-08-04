# Manuscript Correction Speed Plan - 2026-08-01

Owner decisions reflected here: AI runs on every section (no skip-clean
shortcut); the fast engine must reach users with zero popups via one-time
setup; parallel prefetch and prompt trim are approved.

Baseline today: 2.65s per section warm, ~54s for a dense ten-section page,
first section instant after startup prewarm. Model size measured irrelevant
(1.7B to 12B identical per-section time), so quality stays on gemma4-12b.

## Workstream 1 - Signed fast engine, zero popups (owner action + build)

Why: the bundled llama.cpp named-pipe host is the designed fast path
(~0.2s warm raw inference vs ~2.6s through the Ollama HTTP detour), but
Windows Application Control / Smart App Control blocks the unsigned
executable (WinError 4551). There is no legitimate silent way to allow
an unsigned binary - anything that "quietly adds an exception" is both
fragile and the kind of behavior security software should flag. The
correct one-time setup is OURS, not the user's: sign the binaries.

Plan:
1. Curtis picks a signing route (one-time business decision):
   - Azure Trusted Signing (~$9.99/month, cloud HSM, integrates with
     signtool; identity validation for an individual/org), or
   - a classic OV code-signing certificate from a CA (~$200-400/year).
   This also clears the existing product-wide release blocker: the main
   Manuscript.exe and installer are unsigned today.
2. Build pipeline signs, in order: the llama.cpp host exe, every bundled
   runtime DLL, Manuscript.exe, and ManuscriptSetup.exe (signtool +
   timestamping). One PowerShell step in tools/build.py.
3. Installer runs elevated once (it already does); nothing else to ask.
   Signed binaries load cleanly under Smart App Control and WDAC
   reputation policies - the user sees no popup, ever.
4. Verification: clean Windows 11 VM with Smart App Control ON -> install,
   first correction must use backend "llama.cpp" (diagnostics already
   report the active backend), zero prompts. Repeat on a standard-user
   account.
5. Interim on the dev machine only: one manual allow of the host exe so
   the fast path can be measured end to end before the certificate
   arrives.

Expected effect: per-section generation drops from ~2.4s to well under 1s
warm; combined with Workstream 2 a normal page reviews in under 15s.

## Workstream 2 - Parallel section prefetch (code)

Why: background prefetch is a single FIFO lane; sections generate one at
a time. The GPU has headroom for two to three concurrent 4k-context
requests on the 12B pack (16GB VRAM, ~1.5GB extra per context).

Plan:
1. Capability probe at startup: issue two tiny concurrent generations;
   if wall time shows serialization (Ollama server without
   OLLAMA_NUM_PARALLEL), stay at 1 worker - no user configuration and
   no regression. The bundled llama.cpp host (Workstream 1) is under our
   control and gets a parallel slot count in its handshake instead.
2. Widen the HUD AI pipeline from one in-flight request to a small pool
   (2-3):
   - rewrite_palette: replace the single `_active_ai_attempt_id` slot
     with a per-suggestion attempt map; completion, failure, timeout,
     and progress routing are already suggestion-id keyed.
   - window worker: N daemon workers draining the existing queue; results
     stay per-id so review order is untouched.
   - The CURRENT section always gets priority (front of queue) so the
     user-facing wait is never behind background work.
3. Adaptive width: hardware snapshot already reports available VRAM and
   battery; width drops to 1 when VRAM is tight, on battery, or on
   CPU-only machines (deployment-fleet rule: never assume the 4090).
4. Cut the fixed ~0.5s gap between accept and the next request (timer
   coalescing in the palette).
5. Verification gates (all must hold): 101 palette tests extended with
   pool-ordering/failure-interleaving cases; rapid-dictation gate
   (three consecutive runs); dense ten-section journey - target under
   35s total with Ollama, under 15s with the signed host; 20/20 locked
   corpus; full-page tone gate 3 pages x Proofread.

Expected effect: page total roughly halves even before Workstream 1
lands; with it, most sections are ready before the user reaches them.

## Workstream 3 - Prompt trim (code, quality-gated)

Why: every section carries a ~3,500-character instruction block; prefill
costs time on every machine (the weak-hardware fleet most of all) and
long instructions measurably distract small packs.

Plan:
1. Inventory the Proofread prompt: shared preamble, per-tone contract,
   protected-fact inventories, retry rules.
2. Produce a compact Proofread variant (~40-50% shorter) that keeps,
   verbatim: complete-replacement-only output, preserve facts / names /
   numbers / dates / links / quotations, keep paragraph and line-break
   structure, no em dash, no explanations. Cut duplicated phrasing and
   prose that restates the tone list for a command that has no tone.
3. Feature-flag the compact prompt (settings meta, default off) so A/B
   verification is honest.
4. Verification gates before the flag defaults on: 20/20 locked corpus,
   gate-redesign suite, dense ten-section journey with equal-or-better
   errors-fixed count, full-page tone gate unchanged, and the writing
   quality probe on both the 12B and the 1.7B pack (fleet floor).
   Any regression -> flag stays off and the finding is documented.

Expected effect: 0.3-0.5s per section on the 4090; proportionally more
on CPU-only machines.

## Explicitly rejected

- Skip-AI-for-clean-sections: owner decision - AI reviews every section
  because rules alone miss errors that only the model finds.
- Smaller default model: measured no speed benefit on GPU; quality risk
  on the fleet floor is not worth zero gain.
- Silent security exceptions for the unsigned host: not acceptable
  behavior for a commercial product; signing is the fix.

## Order of execution

1. Workstream 2 (pure code, immediately verifiable locally).
2. Workstream 3 behind its flag, verified on two packs.
3. Workstream 1 as soon as the signing decision lands; interim dev-only
   measurement now.

Product-governance note: these change a sale-blocked product's behavior;
release builds still route through the Board-vote gate before any
distribution.
