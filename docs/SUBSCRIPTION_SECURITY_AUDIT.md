# Manuscript subscription security audit

Date: 2026-07-25

## Result

No known critical or high-severity flaw remains in the implemented website dependency tree. Public sale is still blocked by the absence of a trusted public code-signing certificate, placeholder service configuration, missing live payment credentials, and untested live operations.

## Controls verified in code

- Payment-card data stays inside PayPal-hosted subscription controls.
- `PAYPAL_CLIENT_SECRET` exists only in the server runtime. The browser receives
  only the public client ID and configured plan ID.
- Activation accepts only a bounded PayPal subscription identifier, retrieves
  that subscription from PayPal's fixed API origin, and requires an exact match
  to the configured `$5 USD` monthly plan.
- Access accepts only PayPal `ACTIVE` status. Suspended, canceled, expired,
  approval-pending, unknown, and unreachable status fails closed.
- The subscription's PayPal account email is bound to one Manuscript account;
  later account mismatches are rejected.
- License keys carry 160 bits of HMAC-derived material; D1 stores only a SHA-256 hash and short prefix.
- Installation IDs are random. No Windows hardware serial, username, SID, document, transcript, or audio is sent.
- Local license state is DPAPI-protected for the current Windows user and written atomically.
- Desktop network calls use a fixed HTTPS origin, fixed API path, normal TLS verification, no proxies, no redirects, bounded response size, and a timeout.
- Website write routes limit body size. Browser routes reject cross-site posts.
- Activation and API responses use `Cache-Control: no-store`.
- CSP, HSTS, frame blocking, referrer, permissions, and MIME-sniffing headers are present.
- Support requests use length checks, a honeypot, a secret HMAC request fingerprint, a per-fingerprint limit, indexed cleanup, and a 90-day retention cap.
- License-validation request fingerprints use a separate secret HMAC domain, and stale rate-limit buckets are purged through an indexed path.
- npm production audit: zero known vulnerabilities after overriding fixed `sharp` and `postcss` versions.
- Python dependency audit: zero known vulnerabilities after upgrading Pillow from 12.2.0 to 12.3.0.
- Production packaging refuses to run without an explicit certificate thumbprint, signs every packaged PE file and the installer with SHA-256 and an RFC 3161 timestamp, and verifies each signature against Windows Authenticode policy.
- Production builds reject the hidden smoke, workflow-probe, target-probe, and internal-evaluation command paths.

## Open risks

| Severity | Risk | Required action |
|---|---|---|
| Release blocker | No trusted public code-signing certificate is installed; existing artifacts remain unsigned | Obtain the certificate, run the now fail-closed signing build, then test every signed binary under Smart App Control |
| Release blocker | Entitlement origin is a `.invalid` placeholder | Deploy the public service, write its exact HTTPS origin into the resource file, then rebuild |
| Release blocker | PayPal production credentials, webhook ID, and the final `$5 USD` plan are not configured or tested | Complete the sandbox matrix, register the verified webhook route, then configure the production client ID, `PAYPAL_CLIENT_SECRET`, `PAYPAL_PLAN_ID`, and `PAYPAL_WEBHOOK_ID` |
| High | A determined attacker can patch a local Python/PyInstaller client to skip a client-side gate | Code signing, integrity monitoring, server-gated updates/downloads, short recheck windows, and abuse monitoring reduce risk; no desktop DRM can make patching impossible |
| Medium | Strict fail-closed checks can lock out paying users during a PayPal or network outage | Decide whether the launch policy remains strict or uses a short signed offline lease; this is a business/security tradeoff |
| Medium | Support tickets have bounded retention but no alert or staff queue is connected | Add a secured admin workflow, notification, and access logging before launch |
| Medium | Application-level request limiting is basic | Add Cloudflare edge rate limiting, bot controls, and alerting before public launch |
| Medium | CSP allows inline scripts/styles for the current framework output | Move to request nonces or hashes when the runtime supports a tested nonce pipeline |
| Existing | Same-user malware or an administrator can inspect a running app | Not solved by DPAPI or SQLCipher; document the boundary and keep dependencies and signing current |

## Test evidence

- Python subscription, packaging, startup, and ACL slice: 14 passed.
- Final focused privacy, subscription, crypto, ACL, and branding slice: 80 passed with isolated app data.
- Website build and 21 site/security checks: passed.
- npm production and development vulnerability audit: zero known vulnerabilities.
- Python dependency vulnerability audit: zero known vulnerabilities.
- Broad Windows suite excluding the missing Apple source-path collector: 759 passed, 2 skipped, and 6 unrelated existing failures (Android sibling path, three selected-text expectations, shared fixture drift, and unavailable Git build commit in the sandbox).

## Not yet proven

- Real PayPal approval, renewal, suspension, cancellation, refund, dispute, and
  access-revocation lifecycle.
- Public domain, TLS, D1 backup, WAF, monitoring, and secret rotation.
- Signed installer trust on a clean Windows 10/11 device.
- Resistance to binary patching or same-user malware.
- Legal compliance in each country or state where the app will be sold.
