export const ATOMIC_DEVICE_ACTIVATION_SQL = `
  INSERT INTO device_activations (license_id, device_hash)
  SELECT ?, ?
  WHERE EXISTS (
    SELECT 1 FROM device_activations
    WHERE license_id = ? AND device_hash = ?
  ) OR (
    SELECT COUNT(*) FROM device_activations WHERE license_id = ?
  ) < ?
  ON CONFLICT(license_id, device_hash) DO UPDATE SET
    last_seen_at = CURRENT_TIMESTAMP
`;

export const ATOMIC_RATE_LIMIT_SQL = `
  INSERT INTO license_validation_limits (request_fingerprint, minute_bucket, attempts)
  VALUES (?, ?, 1)
  ON CONFLICT(request_fingerprint, minute_bucket) DO UPDATE SET
    attempts = license_validation_limits.attempts + 1
  WHERE license_validation_limits.attempts < ?
`;

export const PURGE_EXPIRED_SUPPORT_TICKETS_SQL = `
  DELETE FROM support_tickets
  WHERE created_at < datetime('now', '-90 days')
`;

export const PURGE_STALE_LICENSE_LIMITS_SQL = `
  DELETE FROM license_validation_limits
  WHERE minute_bucket < ?
`;

export const UPSERT_ACCOUNT_SUBSCRIPTION_SQL = `
  INSERT INTO subscriptions (
    stripe_customer_id, stripe_subscription_id, account_id, account_email,
    status, current_period_end, cancel_at_period_end, updated_at
  ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
  ON CONFLICT(stripe_customer_id) DO UPDATE SET
    stripe_subscription_id = excluded.stripe_subscription_id,
    account_id = COALESCE(subscriptions.account_id, excluded.account_id),
    account_email = CASE
      WHEN subscriptions.account_id = excluded.account_id
        AND excluded.account_email IS NOT NULL
      THEN excluded.account_email
      ELSE COALESCE(subscriptions.account_email, excluded.account_email)
    END,
    status = excluded.status,
    current_period_end = excluded.current_period_end,
    cancel_at_period_end = excluded.cancel_at_period_end,
    updated_at = CURRENT_TIMESTAMP
`;
export const ANONYMIZE_DELETED_ACCOUNT_SQL = `
  UPDATE subscriptions
  SET account_id = NULL, account_email = NULL, updated_at = CURRENT_TIMESTAMP
  WHERE account_id = ?
`;

export const DELETE_ACCOUNT_SUPPORT_SQL = `
  DELETE FROM support_tickets
  WHERE lower(email) = lower(?)
`;