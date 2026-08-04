# Manuscript Adaptive Polish

## Implemented boundary

The existing Python, PySide, UI Automation, and Writing HUD remain. All
correction entry points route through these internal boundaries:

- `PolishEngine.analyze(CorrectionRequest) -> CorrectionResult`
- `HardwarePolicyManager.plan(...) -> ExecutionPlan`
- `ModelPackManager.ensure(ExecutionPlan) -> PolishModelPack`
- `InferenceRuntime.generate(GenerationRequest) -> GenerationResult`

`AdaptiveInferenceRuntime` now prefers the self-contained
`Manuscript.PolishHost`, which owns a current-user-only Windows named pipe and a
verified llama.cpp child runtime. Ollama remains the rollback runtime and the
temporary model-download bridge. A failed host, runtime-package, device,
residency, or acceptance probe falls back without bypassing output validation.

## Local correction cascade

1. Normalize Unicode and conservative whitespace boundaries.
2. Preserve protected spans and user vocabulary.
3. Run existing Manuscript rules, bundled Hunspell English, and SymSpell.
4. Run the bundled INT8 MiniLM ONNX encoder on the CPU with deterministic
   routing heads.
5. Select rules-only or one signed Qwen3 pack from measured hardware.
6. Verify and probe one signed CPU, CUDA, Vulkan, HIP, or SYCL llama.cpp pack.
7. Run a fixed correctness, latency, protected-token, and full-GPU acceptance
   probe before first use on a hardware/runtime/model combination.
8. Reject generative output that changes protected facts, identifiers,
   negations, obligations, ordering, actors, named entities, paragraph
   structure, grammar-mode length, or semantic meaning.
9. Return minimal indexed edits for the existing red-delete/green-insert review.

No external-application correction is silently applied.

## Hardware policy

The policy measures CPU features, CPU threads, total and available RAM, DXGI
adapters, dedicated and available VRAM, NVIDIA driver health, battery state,
and the installed Ollama version. It reserves 1.5 to 4 GB of VRAM by tier and
caps model RAM at the lower of half of total RAM or 70 percent of available
RAM.

The model and runtime catalogs are signed allowlists. Candidate model tiers are Qwen3 1.7B Q4,
4B Q4, 8B Q4, and 14B Q4. The largest resource-safe candidate still has to
pass its local probe. Failed candidates fall back to an already installed
smaller validated pack or to rules and MiniLM. Low RAM, VRAM pressure, an
unhealthy driver, or a low battery causes a visible downgrade; recovery
restores the largest safe tier.

The current 64 GB RAM and RTX 4090 Laptop profile selects the pinned Qwen3 14B
Q4_K_M pack when enough VRAM remains available. The installed b10103 CUDA 13.3
pack reported the actual CUDA device, loaded all 41 of 41 layers on GPU, and
reported 9.31 GB of active GPU buffers. CPU/GPU layer spill is rejected.

## Consent and model integrity

The settings page shows the hardware-selected model and runtime download size.
Download consent is off by default. Model and runtime entries contain pinned
sources, byte sizes, and SHA-256 digests. Both bundled catalogs use the same
RSA-3072 trust root and pinned public-key fingerprint. HTTPS packages use
atomic partial downloads with exact byte-count and SHA-256 verification.
Archives reject traversal, links, expansion bombs, conflicting files, and
post-install corruption. Ollama bridge packs are accepted only when the local
registry digest and quantization match the signed catalog.

The release signing private key is DPAPI-protected outside the repository.
Only its public key and the detached catalog signature ship.

## Privacy and cache

All correction and benchmark prompts stay local. The benchmark cache accepts
only hardware identifiers, driver/runtime/model versions, correctness flags,
latency, backend, resource measurements, and timestamps. It has no field for
source or generated text.

## Runtime control and UI

The llama.cpp server uses five-minute idle sleep and reloads only on demand.
The Ollama rollback retains its five-minute unload. The HUD reports the
selected model, measured backend, measured VRAM when available, loading state,
and a measured ETA when a comparable local sample exists. Instant, Balanced,
Professional, and Auto use the policy budgets defined in `polish_hardware.py`;
all generation requests have a 30-second absolute ceiling.

## Phase 2 host security

- Current-user-only named pipe with a random 256-bit per-launch secret.
- Bounded, length-prefixed JSON with a fixed protocol version.
- Host exits with its parent; child llama.cpp is killed with the host.
- Runtime and model paths are fixed to validated local roots.
- The child binds only to numeric loopback, disables its UI, and receives its
  random API key through its environment rather than the command line.
- Verbose llama.cpp output is captured only until full-GPU residency is proved.
  Every later log line is discarded before any writing request is sent.
- No prompt, source text, generated text, or benchmark text is persisted.

## Pinned local assets

- Sentence Transformers `all-MiniLM-L6-v2`, revision
  `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`, INT8 ONNX model
- LibreOffice English dictionaries, revision
  `da8a7e73fd26a134ad7c6438fa7c310730906b3a`
- SymSpell frequency dictionary, revision
  `c239062ae02961df18ab7da1671d01b4388204e0`
- llama.cpp release `b10103`, commit
  `c588c4f47683e73ad2d69f50480bec6cc85fd0f7`

Licenses and source links are recorded in
`resources/polish/THIRD_PARTY_NOTICES.md`.

## Phase 3 application adapters

The universal small-icon path now recognizes Microsoft Word and Outlook editor
windows, including stale Outlook focus that resolves to the active Word
document. It keeps the stable UI Automation range and draws the exact
screen-space correction underline without changing Office document formatting
or adding entries to the Office undo stack.

The packaged Chrome and Edge extension uses Native Messaging through
`com.manuscript.polish`. It shows the icon only beside editable text controls,
sends bounded correction requests to the local browser host, and always shows a
red-delete/green-insert review before replacement. Password, disabled, and
read-only fields are rejected. Rich contenteditable documents remain
review-only unless a stable text selection can be replaced without destroying
their formatting.

The browser host uses bounded length-prefixed JSON and starts the same local
`PolishEngine`. Production builds require a live active entitlement before
correction; testing-unlocked builds are visibly separate. The installer
registers the current-user Chrome and Edge Native Messaging manifests. No
browser text is persisted.

Windows Text Services Framework remains deferred until live Word, Outlook,
Chrome, and Edge acceptance passes.

## Remaining release gates

The Phase 2 source, self-contained host build, signed package delivery, secure
pipe, CUDA device probe, full-residency check, and local Qwen3 acceptance test
exist. The standalone host is still unsigned. Windows Code Integrity correctly
blocked the freshly rebuilt development EXE. Local development proof therefore
used the same managed host DLL through the trusted installed .NET launcher.
Production must Authenticode-sign the host, app, and installer and rerun
clean-machine Code Integrity tests. HIP, Vulkan, SYCL, and CPU packs are signed
and installable but still need live acceptance on matching hardware.

Phase 3 source and packaging are present. Live Word, Outlook, Chrome, and Edge
acceptance still must pass from a clean signed installation. The Office path
uses a non-mutating exact overlay rather than native document formatting.

No sale release may claim completed Phase 2 or Phase 3 until clean-install,
trusted signing, licensing, offline-runtime, active-subscription, and
backend-specific hardware gates pass.
