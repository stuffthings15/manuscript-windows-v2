# Manuscript sale-readiness checklist

Status: implementation complete; live launch blocked by owner and payment setup.

## Built

- Product landing page with responsive desktop/mobile layout.
- PayPal-hosted subscription approval controls for the `$5 USD` monthly plan.
- PayPal automatic-payment management and cancellation link.
- Server-side live PayPal subscription lookup, exact plan matching, and
  `ACTIVE`-only access.
- D1 account, subscription, hashed license, activation, and support records.
- License issue page after verified checkout.
- Windows activation gate with DPAPI-protected license state.
- Periodic live entitlement recheck; inactive or unreachable status fails closed.
- Privacy policy, subscription terms, EULA, and support form drafts.
- Security headers, request-size limits, same-origin checks, and support throttling.
- Production website build, migration, focused tests, and clean npm vulnerability audit.

## Owner input required before public launch

- Legal seller name and business contact details.
- Public support email or staffed support workflow.
- Final monthly price, currency, refund policy, trial choice, and installation limit.
- PayPal business verification, payout bank, tax details, final `$5 USD`
  recurring plan, recovery contacts, and production credentials.
- Tax registration and calculation process for each sales region. PayPal does
  not decide where the seller must register.
- Final domain name and public Sites deployment approval.
- Exact public origin stored as the hosted `PUBLIC_SITE_URL` value.
- License-service origin written into `resources/subscription.json` before packaging.
- Public privacy, terms, EULA, support, and refund review by qualified counsel for every sales region.

## Release blockers

- The current EXE and installer are unsigned. Obtain a trusted RSA code-signing path and sign both the app binaries and installer.
- The new self-contained Polish host is also unsigned. Windows Code Integrity
  blocked the rebuilt development host exactly as expected. Sign the host,
  verify its signature before launch, and rerun the CUDA/offline/idle-unload
  suite from a clean installation.
- The Chrome/Edge Native Messaging host, extension package, and current-user
  registration are built but not store-signed. Run live Word, Outlook, Chrome,
  and Edge acceptance from the signed clean installation before public claims.
- CUDA 13.3 passed live Qwen3 14B testing on this RTX 4090 Laptop GPU. CPU,
  Vulkan, HIP, and SYCL packages still need matching-hardware acceptance before
  their backends can be claimed.
- Run clean-machine install, upgrade, repair, uninstall, Windows SmartScreen/Smart App Control, malware scan, and entitlement outage tests.
- Run a full PayPal sandbox matrix: successful approval, rejected approval,
  failed renewal, suspension, cancellation, expiration, refund, dispute,
  account mismatch, and access revocation.
- Add edge rate limiting/WAF rules for activation, license validation, and
  support routes.
- Configure encrypted D1 backup/restore, secret rotation, billing alerts,
  support-ticket handling, uptime monitoring, and incident response.
- Publish installer SHA-256 and signed release notes. Never serve an old unsigned installer from the sales page.

## Launch order

1. Complete owner inputs and legal review.
2. Configure the PayPal sandbox client ID, `PAYPAL_CLIENT_SECRET`,
   `PAYPAL_PLAN_ID`, `PAYPAL_WEBHOOK_ID`, and the remaining Sites secrets.
3. Deploy the site privately and run the sandbox matrix.
4. Choose and connect the public domain.
5. Configure the PayPal production client ID, secret, and final active plan.
6. Update the desktop entitlement origin.
7. Sign, rebuild, and test the installer on a clean Windows machine.
8. Upload only the signed build and verify its published hash.
9. Run one real low-cost purchase, activation, portal cancellation, and access-revocation test.
10. Open public sales only after every release blocker is closed.
