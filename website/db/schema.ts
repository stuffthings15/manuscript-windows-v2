import { sql } from "drizzle-orm";
import {
  index,
  integer,
  primaryKey,
  sqliteTable,
  text,
  uniqueIndex,
} from "drizzle-orm/sqlite-core";

export const subscriptions = sqliteTable(
  "subscriptions",
  {
    id: integer("id").primaryKey({ autoIncrement: true }),
    stripeCustomerId: text("stripe_customer_id").notNull(),
    stripeSubscriptionId: text("stripe_subscription_id").notNull(),
    accountId: text("account_id"),
    accountEmail: text("account_email"),
    status: text("status").notNull(),
    currentPeriodEnd: integer("current_period_end"),
    cancelAtPeriodEnd: integer("cancel_at_period_end", { mode: "boolean" }).notNull().default(false),
    updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [
    uniqueIndex("subscriptions_customer_idx").on(table.stripeCustomerId),
    uniqueIndex("subscriptions_stripe_id_idx").on(table.stripeSubscriptionId),
    uniqueIndex("subscriptions_account_id_idx").on(table.accountId),
    uniqueIndex("subscriptions_account_email_idx").on(table.accountEmail),
    index("subscriptions_status_idx").on(table.status),
  ],
);

export const licenses = sqliteTable(
  "licenses",
  {
    id: integer("id").primaryKey({ autoIncrement: true }),
    subscriptionId: integer("subscription_id").notNull().references(() => subscriptions.id, { onDelete: "cascade" }),
    licenseHash: text("license_hash").notNull(),
    licensePrefix: text("license_prefix").notNull(),
    revokedAt: text("revoked_at"),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [
    uniqueIndex("licenses_subscription_idx").on(table.subscriptionId),
    uniqueIndex("licenses_hash_idx").on(table.licenseHash),
  ],
);

export const deviceActivations = sqliteTable(
  "device_activations",
  {
    id: integer("id").primaryKey({ autoIncrement: true }),
    licenseId: integer("license_id").notNull().references(() => licenses.id, { onDelete: "cascade" }),
    deviceHash: text("device_hash").notNull(),
    firstSeenAt: text("first_seen_at").notNull().default(sql`CURRENT_TIMESTAMP`),
    lastSeenAt: text("last_seen_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [uniqueIndex("device_activation_unique_idx").on(table.licenseId, table.deviceHash)],
);

export const webhookEvents = sqliteTable("webhook_events", {
  eventId: text("event_id").primaryKey(),
  eventType: text("event_type").notNull(),
  receivedAt: text("received_at").notNull().default(sql`CURRENT_TIMESTAMP`),
});

export const supportTickets = sqliteTable(
  "support_tickets",
  {
    id: integer("id").primaryKey({ autoIncrement: true }),
    email: text("email").notNull(),
    subject: text("subject").notNull(),
    message: text("message").notNull(),
    requestFingerprint: text("request_fingerprint").notNull(),
    status: text("status").notNull().default("open"),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [
    index("support_fingerprint_idx").on(table.requestFingerprint, table.createdAt),
    index("support_created_at_idx").on(table.createdAt),
  ],
);

export const accountDeletionJobs = sqliteTable(
  "account_deletion_jobs",
  {
    accountId: text("account_id").primaryKey(),
    accountEmail: text("account_email").notNull(),
    attempts: integer("attempts").notNull().default(0),
    lastError: text("last_error"),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
    updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [index("account_deletion_jobs_updated_idx").on(table.updatedAt)],
);
export const licenseValidationLimits = sqliteTable(
  "license_validation_limits",
  {
    requestFingerprint: text("request_fingerprint").notNull(),
    minuteBucket: integer("minute_bucket").notNull(),
    attempts: integer("attempts").notNull().default(1),
  },
  (table) => [
    primaryKey({
      columns: [table.requestFingerprint, table.minuteBucket],
      name: "license_validation_limits_pk",
    }),
    index("license_validation_bucket_idx").on(table.minuteBucket),
  ],
);
