# Manuscript sales and entitlement service

This site sells Manuscript through a $5 monthly PayPal subscription and enforces paid access through a server-side entitlement service.

## Local setup

1. Copy `.dev.vars.example` to `.dev.vars`.
2. Fill in PayPal sandbox values, a random license derivation secret, and the hosted identity-provider values.
3. Run `npm run dev`.

Never commit `.dev.vars`, PayPal credentials, license secrets, customer data, or a production database.

## Required runtime values

- `PAYPAL_CLIENT_ID`: PayPal REST application client ID.
- `PAYPAL_CLIENT_SECRET`: PayPal REST application secret, stored as a hosted secret.
- `PAYPAL_PLAN_ID`: active PayPal plan configured for exactly $5 USD each month.
- `PAYPAL_WEBHOOK_ID`: ID for the PayPal webhook registered to
  `/api/paypal/webhook`.
- `LICENSE_DERIVATION_SECRET`: at least 32 random bytes, stored as a hosted secret.
- `MAX_LICENSED_DEVICES`: optional integer from 1 to 10; default is 2.
- `MAX_LICENSE_VALIDATIONS_PER_MINUTE`: optional integer from 5 to 120; default is 30.
- `PUBLIC_SITE_URL`: exact bare HTTPS production origin.
- `IDENTITY_PROVIDER_URL`: bare HTTPS origin for the hosted Supabase-compatible identity service.
- `IDENTITY_PROVIDER_PUBLIC_KEY`: provider publishable or anonymous client key. This is not a service-role secret.
- `IDENTITY_PROVIDER_ADMIN_KEY`: server-only service-role key used only for verified account deletion. Never expose it to browsers or apps.
- `MAX_ACCOUNT_AUTH_ATTEMPTS_PER_MINUTE`: optional integer from 5 to 30; default is 10.

## Shared account model

- The website creates and signs in one email/password account through a hosted identity provider.
- Manuscript never stores password hashes. The site provides recovery pages, while the identity provider must deliver verification and recovery email, enforce breached-password protection, and apply its own abuse controls.
- The stable provider account UUID, not the mutable email address, owns the PayPal subscription.
- Windows, Android, and future apps present the provider access token to `/api/account/entitlement`.
- Native refresh tokens rotate through `/api/auth/refresh`. Browser tokens stay in Secure, HttpOnly, SameSite cookies.
- Android keeps account tokens in Android Keystore-encrypted storage. Writing and correction text never enter an account request.
- `/delete-account` reauthenticates the user, blocks deletion while billing is active, queues a durable deletion job, removes the identity account, deletes support text, and removes account links from retained payment records.
- Configure a scheduled worker trigger for deletion retries. The worker handler processes up to 20 pending jobs per run.

## Security model

- Checkout uses PayPal's hosted subscription controls.
- Subscription state and the configured plan ID are checked against PayPal on every activation and app recheck.
- Checkout requires a signed-in account and writes a secret-backed stable account-ID binding into PayPal's `custom_id`.
- Activation rejects missing or mismatched account bindings before creating a license.
- License keys are stored only as SHA-256 hashes in D1.
- Installations use random local identifiers, not hardware serial numbers.
- Browser write routes reject cross-site form requests and stream oversized bodies to a hard limit.
- Login and registration use a secret-scoped D1 rate limit without storing raw IP addresses or emails in the limit table.
- License validation uses an atomic per-client rate limit and an atomic device cap.
- Support and license-validation limits use separate secret HMAC fingerprints; support content expires after 90 days and stale validation buckets are purged.
- PayPal access tokens are reused safely, and every PayPal call has a hard timeout and response-size limit.
- API and activation responses are not cached.
- The worker sends CSP, HSTS, frame blocking, content-type, referrer, and permissions headers.
- CSP permits only the PayPal, PayPal Objects, and Venmo origins needed by the hosted checkout while keeping Manuscript itself non-frameable.

## Commands

- `npm run dev`: local server
- `npm test`: production build plus site checks
- `npm run typecheck`: application type check
- `npm run types:check`: generated Cloudflare runtime type check
- `npm run db:generate`: generate D1 migrations
- `npm audit --omit=dev --audit-level=high`: dependency vulnerability check
