import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";

import * as accountIdentity from "../lib/account-identity.ts";
import * as accountSession from "../lib/account-session.ts";
import * as cryptoHelpers from "../lib/crypto.ts";

const identityConfig = {
  origin: "https://identity.example.test",
  publicKey: "public-anon-key-for-tests",
};

test("password sign-in returns one stable account identity for every platform", async () => {
  const requests = [];
  const fetcher = async (url, init) => {
    requests.push({ url: String(url), init });
    return Response.json({
      access_token: "access-token",
      refresh_token: "refresh-token",
      expires_in: 3600,
      user: {
        id: "26fb8e92-c81f-4d2c-a479-796f9a7ceab1",
        email: "owner@example.com",
      },
    });
  };

  for (const platform of ["android", "windows", "macos", "ios", "linux", "web"]) {
    const session = await accountIdentity.signInWithPassword(
      {
        email: " Owner@Example.com ",
        password: "correct horse battery staple",
        platform,
      },
      identityConfig,
      fetcher,
    );
    assert.equal(session.account.id, "26fb8e92-c81f-4d2c-a479-796f9a7ceab1");
    assert.equal(session.account.email, "owner@example.com");
    assert.equal(session.platform, platform);
  }

  assert.equal(requests.length, 6);
  assert.equal(
    requests[0].url,
    "https://identity.example.test/auth/v1/token?grant_type=password",
  );
  assert.equal(requests[0].init.headers.apikey, identityConfig.publicKey);
  assert.deepEqual(JSON.parse(requests[0].init.body), {
    email: "owner@example.com",
    password: "correct horse battery staple",
  });
});

test("account checkout binding survives an email address change", async () => {
  const secret = "checkout-binding-secret-for-tests";
  const accountId = "26fb8e92-c81f-4d2c-a479-796f9a7ceab1";
  const expected = `MSCR2-${createHmac("sha256", secret)
    .update(`manuscript-account-checkout-v2:${accountId}`)
    .digest("hex")}`;

  const binding = await cryptoHelpers.checkoutBindingForAccount(accountId, secret);

  assert.equal(binding, expected);
  assert.equal(
    await cryptoHelpers.checkoutBindingMatchesAccount(binding, accountId, secret),
    true,
  );
  assert.equal(
    await cryptoHelpers.checkoutBindingMatchesAccount(
      binding,
      "8a85abdd-4b63-44e5-9706-f2def62891ce",
      secret,
    ),
    false,
  );
});

test("rejects weak registration passwords before sending them to the provider", async () => {
  let providerCalled = false;
  const fetcher = async () => {
    providerCalled = true;
    return Response.json({});
  };

  await assert.rejects(
    accountIdentity.registerWithPassword(
      {
        email: "owner@example.com",
        password: "too-short",
        platform: "web",
      },
      identityConfig,
      fetcher,
    ),
    /at least 15 characters/,
  );
  assert.equal(providerCalled, false);
});

test("verifies a bearer token into the same stable account identity", async () => {
  const fetcher = async (url, init) => {
    assert.equal(String(url), "https://identity.example.test/auth/v1/user");
    assert.equal(init.headers.Authorization, "Bearer platform-access-token");
    return Response.json({
      id: "26fb8e92-c81f-4d2c-a479-796f9a7ceab1",
      email: "owner@example.com",
    });
  };

  const account = await accountIdentity.accountFromAccessToken(
    "platform-access-token",
    identityConfig,
    fetcher,
  );

  assert.deepEqual(account, {
    id: "26fb8e92-c81f-4d2c-a479-796f9a7ceab1",
    email: "owner@example.com",
    displayName: "owner@example.com",
  });
});


test("website session cookies are secure and inaccessible to page scripts", () => {
  const headers = accountSession.sessionCookieHeaders({
    accessToken: "access-token-value",
    refreshToken: "refresh-token-value",
    expiresIn: 3600,
  });

  assert.equal(headers.length, 2);
  for (const header of headers) {
    assert.match(header, /HttpOnly/);
    assert.match(header, /Secure/);
    assert.match(header, /SameSite=Lax/);
    assert.match(header, /Path=\//);
  }
  assert.match(headers[0], /^ms_access=/);
  assert.match(headers[1], /^ms_refresh=/);
});

test("native apps can use a bearer token while browsers use the secure cookie", () => {
  const nativeRequest = new Request("https://manuscript.example/api/account/entitlement", {
    headers: { Authorization: "Bearer native-access-token-value" },
  });
  const webRequest = new Request("https://manuscript.example/api/account/entitlement", {
    headers: { Cookie: "theme=dark; ms_access=web-access-token-value" },
  });

  assert.equal(
    accountSession.accessTokenFromRequest(nativeRequest),
    "native-access-token-value",
  );
  assert.equal(
    accountSession.accessTokenFromRequest(webRequest),
    "web-access-token-value",
  );
});

test("platform entitlement response never exposes a license key or password", () => {
  const response = accountSession.platformEntitlementResponse(
    {
      id: "26fb8e92-c81f-4d2c-a479-796f9a7ceab1",
      email: "owner@example.com",
      displayName: "Owner",
    },
    {
      active: true,
      status: "ACTIVE",
      checkedAt: "2026-07-28T12:00:00.000Z",
      nextCheckSeconds: 900,
    },
    "android",
  );

  assert.deepEqual(response, {
    account: {
      id: "26fb8e92-c81f-4d2c-a479-796f9a7ceab1",
      email: "owner@example.com",
    },
    entitlement: {
      active: true,
      status: "ACTIVE",
      checkedAt: "2026-07-28T12:00:00.000Z",
      nextCheckSeconds: 900,
    },
    platform: "android",
  });
  assert.equal(JSON.stringify(response).includes("license"), false);
  assert.equal(JSON.stringify(response).includes("password"), false);
});
test("refresh rotates the website session without asking for the password again", async () => {
  const fetcher = async (url, init) => {
    assert.equal(
      String(url),
      "https://identity.example.test/auth/v1/token?grant_type=refresh_token",
    );
    assert.deepEqual(JSON.parse(init.body), { refresh_token: "old-refresh-token-value" });
    return Response.json({
      access_token: "new-access-token-value",
      refresh_token: "new-refresh-token-value",
      expires_in: 3600,
      user: {
        id: "26fb8e92-c81f-4d2c-a479-796f9a7ceab1",
        email: "owner@example.com",
      },
    });
  };

  const session = await accountIdentity.refreshPasswordSession(
    "old-refresh-token-value",
    "web",
    identityConfig,
    fetcher,
  );

  assert.equal(session.accessToken, "new-access-token-value");
  assert.equal(session.refreshToken, "new-refresh-token-value");
  assert.equal(session.account.id, "26fb8e92-c81f-4d2c-a479-796f9a7ceab1");
});
test("registration requires verified email when the provider returns no session", async () => {
  const fetcher = async () => Response.json({
    access_token: null,
    refresh_token: null,
    user: {
      id: "26fb8e92-c81f-4d2c-a479-796f9a7ceab1",
      email: "owner@example.com",
    },
  });

  const result = await accountIdentity.registerWithPassword(
    {
      email: "owner@example.com",
      password: "correct horse battery staple",
      platform: "web",
    },
    identityConfig,
    fetcher,
  );

  assert.deepEqual(result, {
    status: "verification_required",
    account: {
      id: "26fb8e92-c81f-4d2c-a479-796f9a7ceab1",
      email: "owner@example.com",
      displayName: "owner@example.com",
    },
  });
});
test("password recovery sends a fixed HTTPS return path without exposing account existence", async () => {
  const requests = [];
  const fetcher = async (url, init) => {
    requests.push({ url: String(url), init });
    return new Response(null, { status: 200 });
  };

  await accountIdentity.requestPasswordRecovery(
    " Owner@Example.com ",
    "https://manuscript.example/reset-password",
    identityConfig,
    fetcher,
  );

  assert.equal(
    requests[0].url,
    "https://identity.example.test/auth/v1/recover?redirect_to=https%3A%2F%2Fmanuscript.example%2Freset-password",
  );
  assert.deepEqual(JSON.parse(requests[0].init.body), {
    email: "owner@example.com",
  });
});

test("password reset requires a valid recovery token and strong replacement password", async () => {
  let providerCalled = false;
  const fetcher = async (url, init) => {
    providerCalled = true;
    assert.equal(String(url), "https://identity.example.test/auth/v1/user");
    assert.equal(init.method, "PUT");
    assert.equal(init.headers.Authorization, "Bearer recovery-access-token-value");
    assert.deepEqual(JSON.parse(init.body), {
      password: "new correct horse battery staple",
    });
    return Response.json({
      id: "26fb8e92-c81f-4d2c-a479-796f9a7ceab1",
      email: "owner@example.com",
    });
  };

  await assert.rejects(
    accountIdentity.updatePasswordWithRecoveryToken(
      "recovery-access-token-value",
      "too-short",
      identityConfig,
      fetcher,
    ),
    /at least 15 characters/,
  );
  assert.equal(providerCalled, false);

  const account = await accountIdentity.updatePasswordWithRecoveryToken(
    "recovery-access-token-value",
    "new correct horse battery staple",
    identityConfig,
    fetcher,
  );
  assert.equal(account.id, "26fb8e92-c81f-4d2c-a479-796f9a7ceab1");
  assert.equal(providerCalled, true);
});
test("identity deletion uses the fixed provider admin endpoint and treats missing users as deleted", async () => {
  const requests = [];
  const fetcher = async (url, init) => {
    requests.push({ url: String(url), init });
    return new Response(null, { status: requests.length === 1 ? 204 : 404 });
  };

  for (let attempt = 0; attempt < 2; attempt += 1) {
    await accountIdentity.deleteIdentityAccount(
      "26fb8e92-c81f-4d2c-a479-796f9a7ceab1",
      "provider-admin-key-for-tests-only",
      identityConfig,
      fetcher,
    );
  }

  assert.equal(
    requests[0].url,
    "https://identity.example.test/auth/v1/admin/users/26fb8e92-c81f-4d2c-a479-796f9a7ceab1",
  );
  assert.equal(requests[0].init.method, "DELETE");
  assert.equal(
    requests[0].init.headers.Authorization,
    "Bearer provider-admin-key-for-tests-only",
  );
});

test("account deletion removes account links and support text but retains payment records", async () => {
  const sql = await import("../lib/sql-security.ts");
  const db = new DatabaseSync(":memory:");
  db.exec(`CREATE TABLE subscriptions (
    id INTEGER PRIMARY KEY,
    stripe_customer_id TEXT NOT NULL,
    stripe_subscription_id TEXT NOT NULL,
    account_id TEXT,
    account_email TEXT,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  );
  CREATE TABLE support_tickets (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL,
    message TEXT NOT NULL
  );`);
  db.prepare("INSERT INTO subscriptions (id, stripe_customer_id, stripe_subscription_id, account_id, account_email, status) VALUES (?, ?, ?, ?, ?, ?)").run(
    1,
    "PAYER-1",
    "I-SUBSCRIPTION",
    "26fb8e92-c81f-4d2c-a479-796f9a7ceab1",
    "owner@example.com",
    "CANCELLED",
  );
  db.prepare("INSERT INTO support_tickets VALUES (?, ?, ?)").run(
    1,
    "owner@example.com",
    "private support text",
  );

  db.prepare(sql.ANONYMIZE_DELETED_ACCOUNT_SQL).run(
    "26fb8e92-c81f-4d2c-a479-796f9a7ceab1",
  );
  db.prepare(sql.DELETE_ACCOUNT_SUPPORT_SQL).run("owner@example.com");

  assert.deepEqual(
    { ...db.prepare("SELECT stripe_customer_id, stripe_subscription_id, account_id, account_email FROM subscriptions").get() },
    {
      stripe_customer_id: "PAYER-1",
      stripe_subscription_id: "I-SUBSCRIPTION",
      account_id: null,
      account_email: null,
    },
  );
  assert.equal(db.prepare("SELECT COUNT(*) AS count FROM support_tickets").get().count, 0);
});
test("the same account keeps its subscription after changing email", async () => {
  const sql = await import("../lib/sql-security.ts");
  const db = new DatabaseSync(":memory:");
  db.exec(`CREATE TABLE subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stripe_customer_id TEXT NOT NULL UNIQUE,
    stripe_subscription_id TEXT NOT NULL UNIQUE,
    account_id TEXT UNIQUE,
    account_email TEXT UNIQUE,
    status TEXT NOT NULL,
    current_period_end INTEGER,
    cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`);
  const upsert = db.prepare(sql.UPSERT_ACCOUNT_SUBSCRIPTION_SQL);
  upsert.run("payer-1", "I-ONE", "26fb8e92-c81f-4d2c-a479-796f9a7ceab1", "old@example.com", "ACTIVE", 10, 0);
  upsert.run("payer-1", "I-ONE", "26fb8e92-c81f-4d2c-a479-796f9a7ceab1", "new@example.com", "ACTIVE", 20, 0);

  assert.deepEqual(
    { ...db.prepare("SELECT account_id, account_email, current_period_end FROM subscriptions").get() },
    {
      account_id: "26fb8e92-c81f-4d2c-a479-796f9a7ceab1",
      account_email: "new@example.com",
      current_period_end: 20,
    },
  );
});