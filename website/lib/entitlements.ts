import {
  bytesToBase32,
  hmacSha256,
  privateRequestFingerprint,
  requireCheckoutBindingForAccount,
  sha256,
  timingSafeEqual,
} from "./crypto";
import { getRuntimeEnv, requireSecret } from "./runtime";
import {
  ATOMIC_DEVICE_ACTIVATION_SQL,
  ATOMIC_RATE_LIMIT_SQL,
  ANONYMIZE_DELETED_ACCOUNT_SQL,
  DELETE_ACCOUNT_SUPPORT_SQL,
  PURGE_EXPIRED_SUPPORT_TICKETS_SQL,
  PURGE_STALE_LICENSE_LIMITS_SQL,
  UPSERT_ACCOUNT_SUBSCRIPTION_SQL,
} from "./sql-security";
import {
  getSubscription,
  PayPalSubscription,
  subscriptionAllowsAccess,
  subscriptionPeriodEnd,
} from "./paypal";

const LICENSE_PATTERN = /^MSCR-(?:[A-Z2-9]{8}-){3}[A-Z2-9]{8}$/;
const DEVICE_PATTERN = /^[a-f0-9]{64}$/;

let schemaReady: Promise<void> | null = null;

export function getD1(): D1Database {
  const db = getRuntimeEnv().DB;
  if (!db) throw new Error("D1 binding DB is unavailable.");
  return db;
}

export function ensureSchema(): Promise<void> {
  if (!schemaReady) {
    schemaReady = initializeSchema(getD1());
  }
  return schemaReady;
}

async function initializeSchema(db: D1Database): Promise<void> {
  await db.batch([
      db.prepare(`CREATE TABLE IF NOT EXISTS subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stripe_customer_id TEXT NOT NULL UNIQUE,
        stripe_subscription_id TEXT NOT NULL UNIQUE,
        account_id TEXT UNIQUE,
        account_email TEXT UNIQUE,
        status TEXT NOT NULL,
        current_period_end INTEGER,
        cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`),
      db.prepare(`CREATE TABLE IF NOT EXISTS licenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subscription_id INTEGER NOT NULL UNIQUE REFERENCES subscriptions(id) ON DELETE CASCADE,
        license_hash TEXT NOT NULL UNIQUE,
        license_prefix TEXT NOT NULL,
        revoked_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`),
      db.prepare(`CREATE TABLE IF NOT EXISTS device_activations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        license_id INTEGER NOT NULL REFERENCES licenses(id) ON DELETE CASCADE,
        device_hash TEXT NOT NULL,
        first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(license_id, device_hash)
      )`),
      db.prepare(`CREATE TABLE IF NOT EXISTS webhook_events (
        event_id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`),
      db.prepare(`CREATE TABLE IF NOT EXISTS support_tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL,
        subject TEXT NOT NULL,
        message TEXT NOT NULL,
        request_fingerprint TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`),
      db.prepare(`CREATE TABLE IF NOT EXISTS account_deletion_jobs (
        account_id TEXT PRIMARY KEY NOT NULL,
        account_email TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`),
      db.prepare("CREATE INDEX IF NOT EXISTS account_deletion_jobs_updated_idx ON account_deletion_jobs(updated_at)"),      db.prepare(`CREATE TABLE IF NOT EXISTS license_validation_limits (
        request_fingerprint TEXT NOT NULL,
        minute_bucket INTEGER NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY(request_fingerprint, minute_bucket)
      )`),
      db.prepare("CREATE INDEX IF NOT EXISTS subscriptions_status_idx ON subscriptions(status)"),
    db.prepare("CREATE INDEX IF NOT EXISTS support_fingerprint_idx ON support_tickets(request_fingerprint, created_at)"),
    db.prepare("CREATE INDEX IF NOT EXISTS support_created_at_idx ON support_tickets(created_at)"),
    db.prepare("CREATE INDEX IF NOT EXISTS license_validation_bucket_idx ON license_validation_limits(minute_bucket)"),
  ]);
  const columns = await db.prepare("PRAGMA table_info(subscriptions)").all<{ name: string }>();
  if (!columns.results.some((column) => column.name === "account_id")) {
    await db.prepare("ALTER TABLE subscriptions ADD COLUMN account_id TEXT").run();
  }
  if (!columns.results.some((column) => column.name === "account_email")) {
    await db.prepare("ALTER TABLE subscriptions ADD COLUMN account_email TEXT").run();
  }
  await db.prepare(
    "CREATE UNIQUE INDEX IF NOT EXISTS subscriptions_account_id_idx ON subscriptions(account_id)",
  ).run();
  await db.prepare(
    "CREATE UNIQUE INDEX IF NOT EXISTS subscriptions_account_email_idx ON subscriptions(account_email)",
  ).run();
}

export async function createSupportTicket(email: string, subject: string, message: string, clientAddress: string): Promise<void> {
  await ensureSchema();
  const fingerprint = await privateRequestFingerprint(
    "support",
    `${email.toLowerCase()}\n${clientAddress.slice(0, 128)}`,
    requireSecret("LICENSE_DERIVATION_SECRET"),
  );
  const db = getD1();
  await db.prepare(PURGE_EXPIRED_SUPPORT_TICKETS_SQL).run();
  const recent = await db.prepare(`SELECT COUNT(*) AS count FROM support_tickets
    WHERE request_fingerprint = ? AND created_at >= datetime('now', '-1 hour')`)
    .bind(fingerprint).first<{ count: number }>();
  if ((recent?.count ?? 0) >= 5) throw new Error("Support rate limit reached.");
  await db.prepare(`INSERT INTO support_tickets (email, subject, message, request_fingerprint)
    VALUES (?, ?, ?, ?)`).bind(email, subject, message, fingerprint).run();
}

export interface SubscriptionAccount {
  id: string;
  email: string;
}

export async function recordSubscription(
  subscription: PayPalSubscription,
  account?: SubscriptionAccount,
): Promise<number> {
  await ensureSchema();
  const customerId = subscription.subscriber?.payer_id || subscription.id;
  const accountId = account?.id.trim().toLowerCase() || null;
  const normalizedEmail = account?.email.trim().toLowerCase() || null;
  if (accountId) {
    await requireCheckoutBindingForAccount(
      subscription.custom_id ?? "",
      accountId,
      requireSecret("LICENSE_DERIVATION_SECRET"),
    );
  }
  const periodEnd = subscriptionPeriodEnd(subscription);
  const db = getD1();
  await db.prepare(UPSERT_ACCOUNT_SUBSCRIPTION_SQL).bind(
      customerId,
      subscription.id,
      accountId,
      normalizedEmail,
      subscription.status,
      periodEnd,
      0,
    ).run();
  const row = await db.prepare(
    "SELECT id, account_id, account_email FROM subscriptions WHERE stripe_customer_id = ?",
  ).bind(customerId).first<{
    id: number;
    account_id: string | null;
    account_email: string | null;
  }>();
  if (!row) throw new Error("Subscription record could not be created.");
  if (accountId && row.account_id !== accountId) {
    throw new Error("Account ID does not match the subscription owner.");
  }
  if (normalizedEmail && row.account_email !== normalizedEmail) {
    throw new Error("Account email does not match the subscription owner.");
  }
  return row.id;
}
export async function deriveLicenseKey(stripeSubscriptionId: string): Promise<string> {
  const material = await hmacSha256(requireSecret("LICENSE_DERIVATION_SECRET"), `manuscript-license-v1:${stripeSubscriptionId}`);
  const compact = bytesToBase32(material.slice(0, 20)).slice(0, 32);
  return `MSCR-${compact.match(/.{1,8}/g)?.join("-")}`;
}

async function ensureLicense(subscriptionId: number, stripeSubscriptionId: string): Promise<string> {
  const licenseKey = await deriveLicenseKey(stripeSubscriptionId);
  const licenseHash = await sha256(licenseKey);
  await getD1().prepare(`INSERT INTO licenses (subscription_id, license_hash, license_prefix)
    VALUES (?, ?, ?) ON CONFLICT(subscription_id) DO UPDATE SET license_hash = excluded.license_hash`)
    .bind(subscriptionId, licenseHash, licenseKey.slice(0, 14)).run();
  return licenseKey;
}

export async function issueLicenseFromSubscription(subscriptionIdValue: string, account: SubscriptionAccount): Promise<{ licenseKey: string; status: string }> {
  if (!/^I-[A-Z0-9]+$/.test(subscriptionIdValue)) throw new Error("Invalid PayPal subscription.");
  const subscription = await getSubscription(subscriptionIdValue);
  if (subscription.plan_id !== requireSecret("PAYPAL_PLAN_ID")) throw new Error("Wrong subscription plan.");
  if (!subscriptionAllowsAccess(subscription.status)) throw new Error("The subscription is not active.");
  const subscriptionId = await recordSubscription(subscription, account);
  const licenseKey = await ensureLicense(subscriptionId, subscription.id);
  return { licenseKey, status: subscription.status };
}

interface LicenseRow {
  license_id: number;
  stripe_customer_id: string;
  stripe_subscription_id: string;
  revoked_at: string | null;
}

async function findLicense(licenseKey: string): Promise<LicenseRow | null> {
  if (!LICENSE_PATTERN.test(licenseKey)) return null;
  await ensureSchema();
  return getD1().prepare(`SELECT l.id AS license_id, l.revoked_at,
    s.stripe_customer_id, s.stripe_subscription_id
    FROM licenses l JOIN subscriptions s ON s.id = l.subscription_id
    WHERE l.license_hash = ?`).bind(await sha256(licenseKey)).first<LicenseRow>();
}

function maxDevices(): number {
  const parsed = Number.parseInt(getRuntimeEnv().MAX_LICENSED_DEVICES || "2", 10);
  return Number.isInteger(parsed) && parsed >= 1 && parsed <= 10 ? parsed : 2;
}

function maxValidationAttempts(): number {
  const parsed = Number.parseInt(
    getRuntimeEnv().MAX_LICENSE_VALIDATIONS_PER_MINUTE || "30",
    10,
  );
  return Number.isInteger(parsed) && parsed >= 5 && parsed <= 120 ? parsed : 30;
}

export async function consumeLicenseValidationAttempt(clientAddress: string): Promise<boolean> {
  await ensureSchema();
  const fingerprint = await privateRequestFingerprint(
    "license-validation",
    clientAddress.slice(0, 128),
    requireSecret("LICENSE_DERIVATION_SECRET"),
  );
  const minuteBucket = Math.floor(Date.now() / 60_000);
  const db = getD1();
  await db.prepare(PURGE_STALE_LICENSE_LIMITS_SQL)
    .bind(minuteBucket - 1_440).run();
  const result = await db.prepare(ATOMIC_RATE_LIMIT_SQL)
    .bind(fingerprint, minuteBucket, maxValidationAttempts()).run();
  return Boolean(result.meta.changes);
}

export async function consumeAccountAuthAttempt(
  email: string,
  clientAddress: string,
): Promise<boolean> {
  await ensureSchema();
  const fingerprint = await privateRequestFingerprint(
    "account-auth",
    `${email.trim().toLowerCase().slice(0, 254)}\n${clientAddress.slice(0, 128)}`,
    requireSecret("LICENSE_DERIVATION_SECRET"),
  );
  const configured = Number.parseInt(
    getRuntimeEnv().MAX_ACCOUNT_AUTH_ATTEMPTS_PER_MINUTE || "10",
    10,
  );
  const maximum = Number.isInteger(configured) && configured >= 5 && configured <= 30
    ? configured
    : 10;
  const minuteBucket = Math.floor(Date.now() / 60_000);
  const db = getD1();
  await db.prepare(PURGE_STALE_LICENSE_LIMITS_SQL)
    .bind(minuteBucket - 1_440).run();
  const result = await db.prepare(ATOMIC_RATE_LIMIT_SQL)
    .bind(fingerprint, minuteBucket, maximum).run();
  return Boolean(result.meta.changes);
}
export async function validateLicense(licenseKey: string, deviceHash: string) {
  const normalizedKey = licenseKey.trim().toUpperCase();
  if (!DEVICE_PATTERN.test(deviceHash)) return { active: false, reason: "invalid_request" } as const;
  const developmentLicenseHash = getRuntimeEnv().DEVELOPMENT_LICENSE_HASH;
  const developmentDeviceHash = getRuntimeEnv().DEVELOPMENT_DEVICE_HASH;
  if (
    getRuntimeEnv().DEPLOYMENT_MODE === "development" &&
    developmentLicenseHash &&
    developmentDeviceHash &&
    timingSafeEqual(await sha256(normalizedKey), developmentLicenseHash) &&
    timingSafeEqual(deviceHash, developmentDeviceHash)
  ) {
    return {
      active: true,
      status: "developer",
      checkedAt: new Date().toISOString(),
      nextCheckSeconds: 3600,
    } as const;
  }
  const row = await findLicense(normalizedKey);
  if (!row || row.revoked_at) return { active: false, reason: "invalid_license" } as const;

  const subscription = await getSubscription(row.stripe_subscription_id);
  await recordSubscription(subscription);
  if (!subscriptionAllowsAccess(subscription.status)) {
    return { active: false, reason: "subscription_inactive", status: subscription.status } as const;
  }

  const db = getD1();
  const activation = await db.prepare(ATOMIC_DEVICE_ACTIVATION_SQL).bind(
    row.license_id,
    deviceHash,
    row.license_id,
    deviceHash,
    row.license_id,
    maxDevices(),
  ).run();
  if (!activation.meta.changes) {
    return { active: false, reason: "device_limit" } as const;
  }

  return {
    active: true,
    status: subscription.status,
    checkedAt: new Date().toISOString(),
    nextCheckSeconds: 900,
  } as const;
}

export async function customerForLicense(licenseKey: string): Promise<string | null> {
  return (await findLicense(licenseKey.trim().toUpperCase()))?.stripe_customer_id ?? null;
}

export async function accountLicense(account: SubscriptionAccount) {
  await ensureSchema();
  const row = await getD1().prepare(
    "SELECT stripe_subscription_id FROM subscriptions WHERE account_id = ?",
  ).bind(account.id.trim().toLowerCase()).first<{ stripe_subscription_id: string }>();
  if (!row) return null;
  const subscription = await getSubscription(row.stripe_subscription_id);
  if (subscription.plan_id !== requireSecret("PAYPAL_PLAN_ID")) {
    throw new Error("Wrong subscription plan.");
  }
  const subscriptionId = await recordSubscription(subscription, account);
  return {
    active: subscriptionAllowsAccess(subscription.status),
    status: subscription.status,
    checkedAt: new Date().toISOString(),
    nextCheckSeconds: 900,
    licenseKey: await ensureLicense(subscriptionId, subscription.id),
  };
}

export async function accountEntitlement(accountId: string) {
  await ensureSchema();
  const row = await getD1().prepare(
    "SELECT stripe_subscription_id FROM subscriptions WHERE account_id = ?",
  ).bind(accountId.trim().toLowerCase()).first<{ stripe_subscription_id: string }>();
  if (!row) {
    return {
      active: false,
      status: "NONE",
      checkedAt: new Date().toISOString(),
      nextCheckSeconds: 300,
    };
  }
  const subscription = await getSubscription(row.stripe_subscription_id);
  if (subscription.plan_id !== requireSecret("PAYPAL_PLAN_ID")) {
    throw new Error("Wrong subscription plan.");
  }
  await recordSubscription(subscription);
  return {
    active: subscriptionAllowsAccess(subscription.status),
    status: subscription.status,
    checkedAt: new Date().toISOString(),
    nextCheckSeconds: 900,
  };
}
export interface PendingAccountDeletion {
  accountId: string;
  accountEmail: string;
}

export async function queueAccountDeletion(
  account: SubscriptionAccount,
): Promise<void> {
  await ensureSchema();
  await getD1().prepare(`INSERT INTO account_deletion_jobs (
      account_id, account_email, attempts, last_error, updated_at
    ) VALUES (?, ?, 0, NULL, CURRENT_TIMESTAMP)
    ON CONFLICT(account_id) DO UPDATE SET
      account_email = excluded.account_email,
      last_error = NULL,
      updated_at = CURRENT_TIMESTAMP`)
    .bind(account.id.trim().toLowerCase(), account.email.trim().toLowerCase())
    .run();
}

export async function pendingAccountDeletionJobs(
  limit: number,
): Promise<PendingAccountDeletion[]> {
  await ensureSchema();
  const boundedLimit = Math.max(1, Math.min(20, Math.floor(limit)));
  const rows = await getD1().prepare(`SELECT account_id, account_email
      FROM account_deletion_jobs
      ORDER BY updated_at ASC
      LIMIT ?`)
    .bind(boundedLimit)
    .all<{ account_id: string; account_email: string }>();
  return rows.results.map((row) => ({
    accountId: row.account_id,
    accountEmail: row.account_email,
  }));
}

export async function finalizeAccountDeletion(
  accountId: string,
  accountEmail: string,
): Promise<void> {
  await ensureSchema();
  const normalizedId = accountId.trim().toLowerCase();
  const normalizedEmail = accountEmail.trim().toLowerCase();
  await getD1().batch([
    getD1().prepare(ANONYMIZE_DELETED_ACCOUNT_SQL).bind(normalizedId),
    getD1().prepare(DELETE_ACCOUNT_SUPPORT_SQL).bind(normalizedEmail),
    getD1().prepare("DELETE FROM account_deletion_jobs WHERE account_id = ?")
      .bind(normalizedId),
  ]);
}

export async function markAccountDeletionFailure(
  accountId: string,
): Promise<void> {
  await ensureSchema();
  await getD1().prepare(`UPDATE account_deletion_jobs
      SET attempts = attempts + 1,
          last_error = 'processing_failed',
          updated_at = CURRENT_TIMESTAMP
      WHERE account_id = ?`)
    .bind(accountId.trim().toLowerCase())
    .run();
}
export async function recordWebhookEvent(eventId: string, eventType: string): Promise<boolean> {
  await ensureSchema();
  const result = await getD1().prepare(
    "INSERT INTO webhook_events (event_id, event_type) VALUES (?, ?) ON CONFLICT(event_id) DO NOTHING",
  ).bind(eventId, eventType).run();
  return Boolean(result.meta.changes);
}

export async function webhookEventSeen(eventId: string): Promise<boolean> {
  await ensureSchema();
  return Boolean(await getD1().prepare("SELECT event_id FROM webhook_events WHERE event_id = ?")
    .bind(eventId).first());
}
