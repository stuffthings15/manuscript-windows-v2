const encoder = new TextEncoder();

export function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function bytesToBase32(bytes: Uint8Array): string {
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  let bits = 0;
  let value = 0;
  let output = "";
  for (const byte of bytes) {
    value = (value << 8) | byte;
    bits += 8;
    while (bits >= 5) {
      output += alphabet[(value >>> (bits - 5)) & 31];
      bits -= 5;
    }
  }
  if (bits) output += alphabet[(value << (5 - bits)) & 31];
  return output;
}

export async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", encoder.encode(value));
  return bytesToHex(new Uint8Array(digest));
}

export async function hmacSha256(secret: string, value: string): Promise<Uint8Array> {
  const key = await crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return new Uint8Array(await crypto.subtle.sign("HMAC", key, encoder.encode(value)));
}

export async function privateRequestFingerprint(
  scope: "support" | "license-validation" | "account-auth",
  value: string,
  secret: string,
): Promise<string> {
  return bytesToHex(
    await hmacSha256(secret, `manuscript-${scope}-fingerprint-v1:${value}`),
  );
}

export function timingSafeEqual(left: string, right: string): boolean {
  if (left.length !== right.length) return false;
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) difference |= left.charCodeAt(index) ^ right.charCodeAt(index);
  return difference === 0;
}

export async function checkoutBindingForEmail(email: string, secret: string): Promise<string> {
  const normalizedEmail = normalizeAccountEmail(email);
  const material = await hmacSha256(secret, `manuscript-checkout-v1:${normalizedEmail}`);
  return `MSCR1-${bytesToHex(material)}`;
}

export async function checkoutBindingMatchesEmail(
  binding: string,
  email: string,
  secret: string,
): Promise<boolean> {
  try {
    return timingSafeEqual(binding, await checkoutBindingForEmail(email, secret));
  } catch {
    return false;
  }
}

export async function requireCheckoutBindingForEmail(
  binding: string,
  email: string,
  secret: string,
): Promise<void> {
  if (!(await checkoutBindingMatchesEmail(binding, email, secret))) {
    throw new Error("Account does not own this PayPal subscription.");
  }
}
export async function checkoutBindingForAccount(
  accountId: string,
  secret: string,
): Promise<string> {
  const normalizedId = normalizeAccountId(accountId);
  const material = await hmacSha256(
    secret,
    `manuscript-account-checkout-v2:${normalizedId}`,
  );
  return `MSCR2-${bytesToHex(material)}`;
}

export async function checkoutBindingMatchesAccount(
  binding: string,
  accountId: string,
  secret: string,
): Promise<boolean> {
  try {
    return timingSafeEqual(
      binding,
      await checkoutBindingForAccount(accountId, secret),
    );
  } catch {
    return false;
  }
}

export async function requireCheckoutBindingForAccount(
  binding: string,
  accountId: string,
  secret: string,
): Promise<void> {
  if (!(await checkoutBindingMatchesAccount(binding, accountId, secret))) {
    throw new Error("Account does not own this PayPal subscription.");
  }
}

function normalizeAccountEmail(email: string): string {
  const normalized = email.trim().toLowerCase();
  if (
    normalized.length > 254 ||
    !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalized)
  ) {
    throw new Error("A valid account email is required.");
  }
  return normalized;
}
function normalizeAccountId(accountId: string): string {
  const normalized = accountId.trim().toLowerCase();
  if (
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(
      normalized,
    )
  ) {
    throw new Error("A valid account ID is required.");
  }
  return normalized;
}
