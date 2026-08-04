import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("defines the finished Manuscript sales page and metadata", async () => {
  const [page, layout] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
  ]);
  assert.match(layout, /title: "Manuscript/);
  assert.match(page, /Flawless writing powered locally/);
  assert.doesNotMatch(`${page}\n${layout}`, /Forge perfect writing/i);
  assert.match(page, /Start subscription/);
  assert.match(page, /Private by design/);
  assert.doesNotMatch(`${page}\n${layout}`, /for Windows|Manuscript for Windows|WRITING FOR WINDOWS/i);
  assert.match(page, /href="\/privacy"/);
  assert.doesNotMatch(`${page}\n${layout}`, /codex-preview|react-loading-skeleton|Your site is taking shape/);
});

test("keeps payment and entitlement decisions on the server", async () => {
  const [page, paypal, validate, account, packageJson] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../lib/paypal.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/license/validate/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/account/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
  ]);
  assert.match(page, /PayPalSubscribe/);
  assert.match(page, /\$5/);
  assert.doesNotMatch(page, /PAYPAL_CLIENT_SECRET/);
  assert.match(paypal, /PAYPAL_CLIENT_SECRET/);
  assert.match(validate, /validateLicense/);
  assert.match(account, /requireAccountUser/);
  assert.match(account, /accountLicense/);
  assert.doesNotMatch(packageJson, /react-loading-skeleton/);
});

test("fails safely when checkout is not configured and binds subscriptions once", async () => {
  const [page, readme] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../lib/entitlements.ts", import.meta.url), "utf8"),
    readFile(new URL("../README.md", import.meta.url), "utf8"),
  ]);
  assert.match(page, /Checkout setup is pending/);
  assert.doesNotMatch(page, /requireSecret\("PAYPAL_CLIENT_ID"\)/);

  assert.match(readme, /PayPal/);
  assert.doesNotMatch(readme, /Stripe/);
});

test("rejects authentication redirects to auth handlers or other origins", async () => {
  const redirects = await import("../lib/account-redirect.ts");
  assert.equal(redirects.safeAccountReturn("/account?tab=billing"), "/account?tab=billing");
  assert.equal(redirects.safeAccountReturn("/api/auth/refresh"), "/");
  assert.equal(redirects.safeAccountReturn("//attacker.example"), "/");
  assert.equal(redirects.safeAccountReturn("https://attacker.example"), "/");
});
test("provides a public Manuscript account deletion request path", async () => {
  const page = await readFile(
    new URL("../app/delete-account/page.tsx", import.meta.url),
    "utf8",
  );
  assert.match(page, /Delete your Manuscript account/);
  assert.match(page, /action="\/api\/account\/delete"/);
  assert.match(page, /Confirm your password/);
  assert.match(page, /cancel.*PayPal/i);
  assert.match(page, /\/support\?subject=Delete%20account/);
  assert.match(page, /getAccountUser/);
});