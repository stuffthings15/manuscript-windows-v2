import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { readFile } from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";
import vm from "node:vm";

import * as cryptoHelpers from "../lib/crypto.ts";
import * as requestSecurity from "../lib/request-security.ts";

test("developer entitlement is explicitly limited to development deployments", async () => {
  const [runtime, entitlements] = await Promise.all([
    readFile(new URL("../lib/runtime.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/entitlements.ts", import.meta.url), "utf8"),
  ]);

  assert.match(runtime, /DEPLOYMENT_MODE\?: string/);
  assert.match(
    entitlements,
    /getRuntimeEnv\(\)\.DEPLOYMENT_MODE === "development"/,
  );
});

test("binds a PayPal subscription claim to the signed-in account", async () => {
  const secret = "checkout-binding-secret-for-tests";
  const ownerEmail = "Owner@Example.com";
  const expected = `MSCR1-${createHmac("sha256", secret)
    .update("manuscript-checkout-v1:owner@example.com")
    .digest("hex")}`;
  const createBinding = cryptoHelpers.checkoutBindingForEmail;
  const verifyBinding = cryptoHelpers.checkoutBindingMatchesEmail;

  const binding = typeof createBinding === "function"
    ? await createBinding(ownerEmail, secret)
    : null;
  const ownerMatches = typeof verifyBinding === "function"
    ? await verifyBinding(binding ?? "", "owner@example.com", secret)
    : false;
  const attackerMatches = typeof verifyBinding === "function"
    ? await verifyBinding(binding ?? "", "attacker@example.com", secret)
    : true;

  assert.equal(binding, expected);
  assert.equal(ownerMatches, true);
  assert.equal(attackerMatches, false);
});

test("rejects a subscription binding claimed by a different account", async () => {
  const secret = "checkout-binding-secret-for-tests";
  const binding = await cryptoHelpers.checkoutBindingForEmail(
    "owner@example.com",
    secret,
  );
  const requireOwner = cryptoHelpers.requireCheckoutBindingForEmail;
  let errorMessage = "Missing ownership validator.";

  if (typeof requireOwner === "function") {
    try {
      await requireOwner(binding, "attacker@example.com", secret);
      errorMessage = "No error";
    } catch (error) {
      errorMessage = error instanceof Error ? error.message : "Unknown error";
    }
  }

  assert.equal(errorMessage, "Account does not own this PayPal subscription.");
});

test("includes the signed account binding in the PayPal create request", async () => {
  const checkout = await import("../lib/paypal-checkout.ts").catch(() => null);
  const request = checkout?.paypalSubscriptionRequest(
    "P-1234567890ABCDEFGHIJKLMN",
    "MSCR1-binding",
  );

  assert.deepEqual(request, {
    plan_id: "P-1234567890ABCDEFGHIJKLMN",
    custom_id: "MSCR1-binding",
  });
});

test("rejects a request body over the limit when Content-Length is absent", async () => {
  const request = new Request("https://example.test/api/license/validate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value: "x".repeat(1024) }),
  });
  const readJsonBody = requestSecurity.readJsonBody;
  let errorName = "MissingBoundedReader";

  if (typeof readJsonBody === "function") {
    try {
      await readJsonBody(request, 256);
      errorName = "NoError";
    } catch (error) {
      errorName = error instanceof Error ? error.name : "UnknownError";
    }
  }

  assert.equal(request.headers.has("Content-Length"), false);
  assert.equal(errorName, "RequestBodyTooLargeError");
});

test("accepts a bounded JSON body when Content-Length is absent", async () => {
  const request = new Request("https://example.test/api/license/validate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ licenseKey: "small" }),
  });
  const readJsonBody = requestSecurity.readJsonBody;
  const payload = typeof readJsonBody === "function"
    ? await readJsonBody(request, 256)
    : null;

  assert.deepEqual(payload, { licenseKey: "small" });
});

test("rejects browser form posts with no same-origin proof", () => {
  const url = "https://manuscript.example/api/support";
  assert.equal(
    requestSecurity.isSameOriginBrowserRequest(new Request(url, { method: "POST" })),
    false,
  );
  assert.equal(
    requestSecurity.isSameOriginBrowserRequest(new Request(url, {
      method: "POST",
      headers: { Origin: "https://manuscript.example" },
    })),
    true,
  );
  assert.equal(
    requestSecurity.isSameOriginBrowserRequest(new Request(url, {
      method: "POST",
      headers: { "Sec-Fetch-Site": "same-origin" },
    })),
    true,
  );
  assert.equal(
    requestSecurity.isSameOriginBrowserRequest(new Request(url, {
      method: "POST",
      headers: { Origin: "https://attacker.example" },
    })),
    false,
  );
});

test("atomically enforces the device cap while allowing an existing device", async () => {
  const sqlSecurity = await import("../lib/sql-security.ts").catch(() => null);
  assert.ok(sqlSecurity, "Atomic device activation SQL is missing.");

  const db = new DatabaseSync(":memory:");
  db.exec(`CREATE TABLE device_activations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    license_id INTEGER NOT NULL,
    device_hash TEXT NOT NULL,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(license_id, device_hash)
  )`);
  const statement = db.prepare(sqlSecurity.ATOMIC_DEVICE_ACTIVATION_SQL);
  const activate = (deviceHash, limit = 2) =>
    Number(statement.run(7, deviceHash, 7, deviceHash, 7, limit).changes);

  assert.equal(activate("device-a"), 1);
  assert.equal(activate("device-b"), 1);
  assert.equal(activate("device-c"), 0);
  assert.equal(activate("device-a"), 1);
  assert.equal(
    db.prepare("SELECT COUNT(*) AS count FROM device_activations WHERE license_id = 7")
      .get().count,
    2,
  );
});

test("atomically caps license validation attempts per time bucket", async () => {
  const sqlSecurity = await import("../lib/sql-security.ts").catch(() => null);
  assert.ok(sqlSecurity, "Atomic rate-limit SQL is missing.");

  const db = new DatabaseSync(":memory:");
  db.exec(`CREATE TABLE license_validation_limits (
    request_fingerprint TEXT NOT NULL,
    minute_bucket INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(request_fingerprint, minute_bucket)
  )`);
  const statement = db.prepare(sqlSecurity.ATOMIC_RATE_LIMIT_SQL);
  const consume = (fingerprint, bucket = 100, limit = 3) =>
    Number(statement.run(fingerprint, bucket, limit).changes);

  assert.equal(consume("client-a"), 1);
  assert.equal(consume("client-a"), 1);
  assert.equal(consume("client-a"), 1);
  assert.equal(consume("client-a"), 0);
  assert.equal(consume("client-b"), 1);
  assert.equal(consume("client-a", 101), 1);
});

test("reuses one PayPal access token until it nears expiration", async () => {
  const paypalClient = await import("../lib/paypal-client.ts").catch(() => null);
  assert.ok(paypalClient, "PayPal client with token caching is missing.");

  const requests = [];
  const subscription = {
    id: "I-SUBSCRIPTION1",
    plan_id: "P-1234567890ABCDEFGHIJKLMN",
    custom_id: "MSCR1-binding",
    status: "ACTIVE",
    subscriber: {
      payer_id: "ABCDEFGHJKLMN",
      email_address: "owner@example.com",
    },
    billing_info: { next_billing_time: "2026-08-25T00:00:00Z" },
  };
  const fetchImpl = async (input) => {
    const url = String(input);
    requests.push(url);
    if (url.endsWith("/v1/oauth2/token")) {
      return Response.json({
        scope: "https://uri.paypal.com/services/subscriptions",
        access_token: "access-token",
        token_type: "Bearer",
        app_id: "APP-123",
        expires_in: 3600,
        nonce: "nonce",
      });
    }
    return Response.json(subscription);
  };
  const client = paypalClient.createPayPalClient({
    clientId: "client-id",
    clientSecret: "client-secret",
    fetchImpl,
    now: () => 1_000_000,
  });

  assert.deepEqual(await client.getSubscription(subscription.id), subscription);
  assert.deepEqual(await client.getSubscription(subscription.id), subscription);
  assert.equal(
    requests.filter((url) => url.endsWith("/v1/oauth2/token")).length,
    1,
  );
  assert.equal(
    requests.filter((url) => url.includes("/v1/billing/subscriptions/")).length,
    2,
  );
});

test("verifies PayPal webhook signatures through the fixed PayPal API", async () => {
  const paypalClient = await import("../lib/paypal-client.ts").catch(() => null);
  assert.ok(paypalClient, "PayPal webhook verification client is missing.");

  const requests = [];
  const fetchImpl = async (input, init = {}) => {
    requests.push({ url: String(input), init });
    if (String(input).endsWith("/v1/oauth2/token")) {
      return Response.json({ access_token: "access-token", expires_in: 3600 });
    }
    return Response.json({ verification_status: "SUCCESS" });
  };
  const client = paypalClient.createPayPalClient({
    clientId: "client-id",
    clientSecret: "client-secret",
    fetchImpl,
  });
  const webhookEvent = {
    id: "WH-123",
    event_type: "BILLING.SUBSCRIPTION.CANCELLED",
    resource: { id: "I-SUBSCRIPTION1" },
  };
  const verified = await client.verifyWebhookSignature({
    auth_algo: "SHA256withRSA",
    cert_url: "https://api.paypal.com/v1/notifications/certs/CERT-123",
    transmission_id: "transmission-123",
    transmission_sig: "signature",
    transmission_time: "2026-07-28T12:00:00Z",
    webhook_id: "webhook-123",
    webhook_event: webhookEvent,
  });

  assert.equal(verified, true);
  assert.equal(
    requests[1].url,
    "https://api-m.paypal.com/v1/notifications/verify-webhook-signature",
  );
  assert.deepEqual(JSON.parse(requests[1].init.body), {
    auth_algo: "SHA256withRSA",
    cert_url: "https://api.paypal.com/v1/notifications/certs/CERT-123",
    transmission_id: "transmission-123",
    transmission_sig: "signature",
    transmission_time: "2026-07-28T12:00:00Z",
    webhook_id: "webhook-123",
    webhook_event: webhookEvent,
  });
});

test("bounds and validates PayPal webhook verification metadata", async () => {
  const webhook = await import("../lib/paypal-webhook.ts").catch(() => null);
  assert.ok(webhook, "PayPal webhook validation helpers are missing.");
  const event = {
    id: "WH-123",
    event_type: "BILLING.SUBSCRIPTION.SUSPENDED",
    resource: { id: "I-SUBSCRIPTION1" },
  };
  const headers = new Headers({
    "PAYPAL-AUTH-ALGO": "SHA256withRSA",
    "PAYPAL-CERT-URL": "https://api.paypal.com/v1/notifications/certs/CERT-123",
    "PAYPAL-TRANSMISSION-ID": "transmission-123",
    "PAYPAL-TRANSMISSION-SIG": "signature",
    "PAYPAL-TRANSMISSION-TIME": "2026-07-28T12:00:00Z",
  });

  assert.equal(
    webhook.paypalWebhookVerification(headers, event, "webhook-123").webhook_id,
    "webhook-123",
  );
  assert.equal(webhook.subscriptionIdFromWebhook(event), "I-SUBSCRIPTION1");
  headers.set("PAYPAL-CERT-URL", "https://attacker.example/cert");
  assert.throws(
    () => webhook.paypalWebhookVerification(headers, event, "webhook-123"),
    /PayPal webhook metadata is invalid/,
  );
});
test("verifies and deduplicates PayPal webhooks before changing billing state", async () => {
  const route = await readFile(
    new URL("../app/api/paypal/webhook/route.ts", import.meta.url),
    "utf8",
  ).catch(() => "");

  assert.match(route, /readJsonBody<unknown>\(request, 65_536\)/);
  assert.match(route, /verifyPayPalWebhook\(request\.headers, event\)/);
  assert.match(route, /webhookEventSeen\(event\.id\)/);
  assert.match(route, /subscription\.plan_id !== requireSecret\("PAYPAL_PLAN_ID"\)/);
  assert.ok(
    route.indexOf("verifyPayPalWebhook(request.headers, event)") <
      route.indexOf("recordWebhookEvent(event.id, event.event_type)"),
  );
});
test("reports a PayPal timeout instead of waiting without a limit", async () => {
  const paypalClient = await import("../lib/paypal-client.ts").catch(() => null);
  assert.ok(paypalClient, "PayPal client with request timeout is missing.");

  const fetchImpl = (_input, init) => new Promise((_resolve, reject) => {
    init?.signal?.addEventListener(
      "abort",
      () => reject(init.signal.reason),
      { once: true },
    );
  });
  const client = paypalClient.createPayPalClient({
    clientId: "client-id",
    clientSecret: "client-secret",
    fetchImpl,
    timeoutMs: 5,
  });

  await assert.rejects(
    () => client.getSubscription("I-SUBSCRIPTION1"),
    (error) => error?.name === "PayPalTimeoutError",
  );
});

test("blocks Manuscript on secret, payment, password, and OTP fields", async () => {
  const source = await readFile(
    new URL("../../browser-extension/field-security.js", import.meta.url),
    "utf8",
  ).catch(() => "");
  const context = { globalThis: {} };
  if (source) vm.runInNewContext(source, context);
  const isSensitive = context.globalThis.ManuscriptFieldSecurity
    ?.isSensitiveFieldDescriptor;

  assert.equal(typeof isSensitive, "function");
  for (const descriptor of [
    { type: "password" },
    { type: "text", autocomplete: "one-time-code" },
    { type: "text", autocomplete: "cc-number" },
    { type: "text", name: "client_secret" },
    { type: "text", id: "api-token" },
    { type: "text", ariaLabel: "Recovery code" },
  ]) {
    assert.equal(isSensitive(descriptor), true);
  }
  for (const descriptor of [
    { type: "text", name: "document_title" },
    { type: "email", autocomplete: "email" },
    { type: "search", ariaLabel: "Search documents" },
  ]) {
    assert.equal(isSensitive(descriptor), false);
  }
});

test("allows required PayPal origins without weakening frame protection", async () => {
  const securityHeaders = await import("../worker/security-headers.ts")
    .catch(() => null);
  const headers = securityHeaders?.securityHeaderEntries("/");

  assert.equal(headers?.["Cross-Origin-Opener-Policy"], "same-origin-allow-popups");
  assert.equal(headers?.["X-Frame-Options"], "DENY");
  assert.match(
    headers?.["Content-Security-Policy"] ?? "",
    /script-src 'self' https:\/\/\*\.paypal\.com https:\/\/\*\.paypalobjects\.com https:\/\/\*\.venmo\.com 'unsafe-inline'/,
  );
  assert.match(
    headers?.["Content-Security-Policy"] ?? "",
    /connect-src 'self' https:\/\/\*\.paypal\.com https:\/\/\*\.paypalobjects\.com https:\/\/\*\.venmo\.com/,
  );
  assert.match(
    headers?.["Content-Security-Policy"] ?? "",
    /frame-src 'self' https:\/\/\*\.paypal\.com https:\/\/\*\.paypalobjects\.com https:\/\/\*\.venmo\.com/,
  );
  assert.match(
    headers?.["Content-Security-Policy"] ?? "",
    /frame-ancestors 'none'/,
  );
});

test("uses secret-scoped request fingerprints and bounded security retention", async () => {
  const { privateRequestFingerprint, sha256 } = await import("../lib/crypto.ts");
  const {
    PURGE_EXPIRED_SUPPORT_TICKETS_SQL,
    PURGE_STALE_LICENSE_LIMITS_SQL,
  } = await import("../lib/sql-security.ts");
  const source = await readFile(
    new URL("../lib/entitlements.ts", import.meta.url),
    "utf8",
  );

  const value = "person@example.com\n203.0.113.10";
  const secret = "a-production-secret-that-is-long-enough";
  const support = await privateRequestFingerprint("support", value, secret);
  const validation = await privateRequestFingerprint(
    "license-validation",
    value,
    secret,
  );

  assert.match(support, /^[a-f0-9]{64}$/);
  assert.notEqual(support, await sha256(value));
  assert.notEqual(support, validation);
  assert.match(PURGE_EXPIRED_SUPPORT_TICKETS_SQL, /DELETE FROM support_tickets/);
  assert.match(PURGE_EXPIRED_SUPPORT_TICKETS_SQL, /-90 days/);
  assert.match(
    PURGE_STALE_LICENSE_LIMITS_SQL,
    /DELETE FROM license_validation_limits/,
  );
  assert.match(source, /privateRequestFingerprint\(\s*"support"/);
  assert.match(source, /privateRequestFingerprint\(\s*"license-validation"/);
  assert.match(source, /PURGE_EXPIRED_SUPPORT_TICKETS_SQL/);
  assert.match(source, /PURGE_STALE_LICENSE_LIMITS_SQL/);
});
