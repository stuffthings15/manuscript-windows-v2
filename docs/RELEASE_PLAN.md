# Release plan - Manuscript Windows Version 2

Written 2026-09-14. Target: public release.

The 2026-09-10 verdict in `AI Brain/Echo-Link/memory.md` still stands: the
software is ready, the release is not. Nothing outstanding is a correction
defect. What follows is the work between here and a signed installer a stranger
can download.

State at the time of writing: branch `codex/windows-reliability-acceptance`,
head `abea826`, suite 2840 passed 2 skipped, ruff clean, PR #1 open. The only
artifact on disk is `ManuscriptV2TestingSetup.exe` dated 2026-08-16, which
predates every fix since and has internal commands enabled. It is not
shippable and must not be published.

## Track A - only Curtis can do these

Nothing in Track B produces a downloadable product until A1 and A2 land.
`tools/build.py` refuses a production build without both.

### A1. Code-signing identity

`_resolve_signing` (tools/build.py:284) already prefers Azure Trusted Signing,
now Azure Artifact Signing, over a SHA-1 thumbprint. The signing command is
written: `/dlib`, `/fd SHA256`, `/td SHA256`, `/tr timestamp.acs.microsoft.com`.
The cheap path is already coded and has never been used.

Recommended: Azure Artifact Signing. From 9.99 USD/month, no hardware token,
open to US self-employed individuals since the three-year tax-history rule was
dropped. Identity validation takes a few business days.

The honest cost: it does NOT grant instant SmartScreen trust. Reputation
accrues to the verified publisher identity across releases, and developers have
reported warnings returning when Microsoft rotated to a new 2026 intermediate
CA. An EV certificate is the only option that buys silence on day one, at
roughly 250-450 USD/year plus a shipped HSM token.

Wiring once the account exists:

- `MANUSCRIPT_AZURE_SIGNING_METADATA` - path to the Trusted Signing
  `metadata.json`
- `MANUSCRIPT_AZURE_SIGNING_DLIB` - path to `Azure.CodeSigning.Dlib.dll`

Or, for the certificate path, `MANUSCRIPT_SIGNING_THUMBPRINT`, exactly 40 hex
characters.

Start this first. It has the longest lead time and it gates A2's verification,
B4, and every artifact.

### A2. Subscription origin

`resources/subscription.json` reads:

```json
{ "origin": "https://replace-before-release.invalid" }
```

`_write_subscription_manifest` (tools/build.py:394) rejects any production build
whose origin fails `_is_public_https_origin`. This is a commercial decision -
which public HTTPS host serves entitlement - not a string to invent.

`website/` in this repository already carries the sales and subscription
foundation: Stripe checkout and portal routes, signed webhooks, D1 entitlements,
licence activation, and `app/eula`, `app/terms`, `app/privacy`, `app/support`.
So the origin most likely already has a home. Confirm the deployed hostname and
put it here.

### A3. Microphone dictation

Ten seconds of real speech through the bundled host closes the second half of
the 2026-09-01 gate. An agent cannot speak. Everything else in that gate was
satisfied on 2026-09-10.

## Track B - agent work, ordered

### B1. Update mechanism

Nothing exists. A search of `src/` for `check_for_update`, `auto_update`,
`autoupdate`, `updater`, `UpdateChannel` and `latest_version` returns zero
matches. A shipped 0.1.0 can therefore never become 0.1.1, which is the single
largest structural gap in the release.

Constraint that shapes the whole design: this product makes no network calls in
the correction path and sends no telemetry. An update check is a network call.
It must therefore be opt-in, outside the correction path, and must never carry
the user's text, document names, or anything identifying beyond the version it
is asking about.

Proposed shape:

- A signed version manifest served from the A2 origin.
- A check that runs on explicit user action first, and only later on a
  schedule, if at all.
- Signature verification against the publisher identity from A1 before any
  download is trusted.
- The installer applies the update; the app never patches itself in place.

Tests must cover: a tampered manifest is refused, a downgrade is refused, a
check with no network fails quietly and changes nothing, and the correction
path issues no request in any of those cases.

This is first because it has no dependency on A1 to begin, and because
shipping without it is irreversible in a way the others are not.

### B2. EULA and consent surface

`website/app/eula` exists. What does not exist is proof the installed product
surfaces it. The work: confirm the EULA text is final, show it at install time
with a recorded acceptance, and make it reachable from inside the app. Small,
and it removes a legal item from the checklist.

### B3. AMD, Intel and ARM coverage

HIP, Vulkan and SYCL packs ship today with no tests. `polish_hardware.py` builds
the `ExecutionPlan` that carries every hardware decision, and
`tests/test_polish_hardware.py` exists as the place this belongs - extend it
rather than starting a parallel file.

The declared hardware floor is "GPU required, small GPU degrades to a smaller
pack". Today that is an assumption. The tests must make it a measured property:
each vendor path selects the pack it claims, a small-VRAM device degrades rather
than failing, and a machine below the floor is refused with a reason the user
can act on.

### B4. Drop the Ollama prerequisite

Ollama remains a model source, not a runtime. `correction.py` still carries
`OllamaRefiner`. Removing the prerequisite means re-issuing the model catalog
against direct GGUF URLs.

Sequenced last because the catalog re-issue needs a signature, so it cannot
complete before A1. The URL and integrity work can be built and tested ahead of
the certificate; only the signing step waits.

## Rules this plan does not get to bend

- Full suite before every commit. Baseline 2840 passed, 2 skipped. A drop is a
  regression.
- One commit per verified change, with its regression test, staged with
  `git add -p`.
- Every regression test must be observed failing without its fix. A test that
  passes against unfixed code is decoration; one was found on 2026-09-12.
- Correction quality is measured through Word, never headlessly, and long Word
  sessions are asked for before they take the screen.
- No em dashes anywhere.

## Known open item, carried not closed

`src/echo_link_app/window.py` holds 28 uncommitted lines, comment only. They
record an unresolved defect: for a single-paragraph section a failed tone
request falls through to the deterministic repair, because
`existing_correction != source.strip()` cannot be true once
`correction_for_section` supplies the same string. It was reproduced, not
reasoned about. The fix is probably to drop the `!=` test, but that changes
which text a writer receives when a tone fails, and this project measures
correction quality through Word. It needs A3's hardware, or at least a Word
session, before anyone touches it.
