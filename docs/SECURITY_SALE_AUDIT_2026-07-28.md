# Manuscript Security and Sale Audit

Date: 2026-07-29

## Verdict

**Not safe to open for public sale yet.**

The local code has strong security controls and the confirmed code defects found
in this audit were repaired. Public sale remains blocked by unsigned and
testing-unlocked binaries, an unconfigured identity provider, an unconfigured
private website, missing live PayPal proof, and uncompleted clean-machine and
operational release checks.

## Repairs completed in this audit

- Updated the website runtime and build dependency chain.
- Replaced the vulnerable ESLint dependency chain with pinned Oxlint 1.76.0.
- Removed all known npm production and development dependency advisories.
- Limited the permanent developer entitlement to deployments explicitly marked
  `DEPLOYMENT_MODE=development`.
- Added a bounded PayPal webhook route that validates metadata, verifies the
  signature through PayPal's fixed API, rejects unknown plans, refreshes billing
  state, and deduplicates event IDs.
- Made production packaging reject placeholder, non-HTTPS, credential-bearing,
  path-bearing, query-bearing, fragment-bearing, and `.invalid` entitlement
  origins.
- Added regression tests for both production controls.
- Added one stable account identity for web, Android, Windows licensing, and
  future platforms.
- Added email registration, sign-in, email verification handling, token
  refresh, secure browser cookies, native bearer tokens, and bounded account
  rate limits.
- Changed subscription ownership from mutable email addresses to stable account
  IDs. Email changes no longer detach a paid subscription.
- Added an Android account screen and production subscription gate. Tokens are
  encrypted with Android Keystore AES-256-GCM and are never persisted as plain
  text.
- Added rate-limited password recovery, strong replacement-password checks,
  enumeration-resistant responses, and website plus Android recovery entry points.
- Replaced manual-only deletion with reauthentication, active-billing blocking,
  durable deletion jobs, provider deletion, support-text removal, payment-record
  anonymization, and scheduled retry code.
- Corrected Android privacy and Data Safety records to disclose account and
  subscription processing.
- Repaired the Android accessibility overlay handoff so a failed correction
  panel does not remove the working icon first.

## Verified controls

- Subscription enforcement fails closed unless a package is explicitly marked
  `testing-unlocked`.
- Production packaging requires a signing certificate thumbprint and verifies
  every packaged PE signature after signing.
- The desktop entitlement client uses one fixed HTTPS origin and API path,
  disables proxies and redirects, validates TLS normally, limits responses, and
  bounds timeouts.
- License and installation state is protected by current-user Windows DPAPI and
  written atomically.
- SQLCipher database encryption, DPAPI key wrapping, private-data defaults,
  purge behavior, local-path restrictions, ACL checks, and export redaction pass
  the focused security suite.
- PayPal credentials stay server-side. Access requires a live subscription
  lookup, the exact configured plan, and `ACTIVE` status.
- License values are hashed in D1. Device activation and validation limits use
  atomic SQL.
- Website APIs bound request sizes, fail closed, return no-store responses, and
  enforce same-origin checks where browser forms require them.
- The browser extension blocks password, payment-card, OTP, private-key, seed
  phrase, and token fields.
- The ignored local `.dev.vars` file is readable only by the current user,
  SYSTEM, and local Administrators.
- A redacted secret-pattern scan found no candidate committed source file.

## Test evidence

- Desktop focused security gate: **72 passed** on the current source.
- Website build, rendered-page, account, and security gate: **36 passed**.
- Website TypeScript check: **passed**.
- Website worker-type freshness check: **passed**.
- Oxlint with warnings denied: **passed**.
- npm full dependency audit: **0 known vulnerabilities**.
- Android release Kotlin compile and **305 unit tests passed**.
- Android release lint: **0 errors and 67 warnings**. The warnings remain
  technical debt and include existing deprecations and accessibility guidance.
- Python `pip-audit` against `requirements.txt`: **0 known vulnerabilities**.
- Bandit scan: **0 high**, **6 medium**, and **101 low** findings across 43,779
  lines. The six medium findings are constant or allowlisted SQL identifiers,
  fixed column choices, and parameterized values. No user-controlled SQL
  identifier was found.
- Microsoft Defender was enabled with current signatures. Its custom scan
  recorded no detection for `ManuscriptSetup.exe`.
- Targeted secret scan: **no candidate source file**.
- Changed-file whitespace check: **passed**.

## Confirmed release blockers

1. `resources/subscription.json` still uses
   `https://replace-before-release.invalid`.
2. The current app and installer are explicitly `testing-unlocked`.
3. `Manuscript.exe`, `Manuscript.PolishHost.exe`,
   `ManuscriptBrowserHost.exe`, `ManuscriptSetup.exe`, and
   `ManuscriptTestingSetup.exe` are not Authenticode signed.
4. The two installed code-signing certificates are self-signed and are not a
   public trust path.
5. The live Sites project uses custom owner-only access, has no custom domain,
   and has zero configured runtime environment variables.
6. No production identity-provider project, URL, public client key, server-only
   administrator key, SMTP delivery, verified-email template, recovery template,
   breached-password protection, deletion retry trigger, or live account-lifecycle
   proof is configured.
7. PayPal live credentials and the active $5 USD monthly plan were verified
   locally. They are not securely entered in Sites, and the webhook ID is still
   missing. Public site origin, license secret, and operational limits also
   remain absent from Sites.
8. No live PayPal sandbox matrix or real purchase, renewal, cancellation,
   refund, dispute, failed-payment, expiration, and access-revocation evidence
   exists.
9. The webhook receiver now exists but is not registered with PayPal or proven
   by signed sandbox and production deliveries.
10. The new account and entitlement code is not deployed. Android sign-in,
    renewal, cancellation, expiration, and revocation are not proven on a
    physical release device.
11. Google Play App access reviewer credentials, the final Data Safety form,
    and a completed deletion request are not proven.
12. Edge WAF and rate limits, D1 backup and restore, secret rotation, uptime
    alerts, billing alerts, incident response, and staffed support handling are
    not proven.
13. Signed clean-install, upgrade, repair, rollback, uninstall, SmartScreen,
    Smart App Control, browser-store package, Word, Outlook, Chrome, and Edge
    acceptance remain unproven.
14. Seller identity, support contact, refund policy, tax handling, privacy
    policy, terms, EULA, and regional legal review still require owner and
    qualified-counsel approval.

## Required release order

1. Obtain a publicly trusted RSA code-signing certificate.
2. Configure the production identity provider, verified email, password
   recovery, account deletion, abuse protection, and reviewer test account.
3. Configure and prove the PayPal sandbox lifecycle, including verified
   webhooks.
4. Configure the private Sites environment and D1 operational controls.
5. Deploy the new account code privately and prove web plus Android account and
   subscription state transitions.
6. Set the exact public HTTPS origin in `resources/subscription.json`.
7. Build the subscription-enforced production package. The build must fail if
   signing or the public origin is missing.
8. Verify every binary signature and publish SHA-256 hashes.
9. Run the signed clean-machine Windows 10 and 11 matrix.
10. Run one real low-cost purchase through cancellation and access revocation
    on web, Windows, and an Android release device.
11. Complete legal, tax, refund, privacy, support, backup, monitoring, and
    incident-response approval.
12. Only then change the website from private access to public.
