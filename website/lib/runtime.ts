import { env } from "cloudflare:workers";

export interface RuntimeEnv {
  DB: D1Database;
  PAYPAL_CLIENT_ID?: string;
  PAYPAL_CLIENT_SECRET?: string;
  PAYPAL_PLAN_ID?: string;
  PAYPAL_WEBHOOK_ID?: string;
  DEVELOPMENT_LICENSE_HASH?: string;
  DEVELOPMENT_DEVICE_HASH?: string;
  DEPLOYMENT_MODE?: string;
  LICENSE_DERIVATION_SECRET?: string;
  MAX_LICENSED_DEVICES?: string;
  MAX_LICENSE_VALIDATIONS_PER_MINUTE?: string;
  MAX_ACCOUNT_AUTH_ATTEMPTS_PER_MINUTE?: string;
  PUBLIC_SITE_URL?: string;
  IDENTITY_PROVIDER_URL?: string;
  IDENTITY_PROVIDER_PUBLIC_KEY?: string;
  IDENTITY_PROVIDER_ADMIN_KEY?: string;
}

export function publicSiteOrigin(requestUrl: string): string {
  const requestOrigin = new URL(requestUrl).origin;
  const configured = getRuntimeEnv().PUBLIC_SITE_URL;
  const candidate = configured || requestOrigin;
  const parsed = new URL(candidate);
  const local = parsed.hostname === "localhost" || parsed.hostname === "127.0.0.1";
  if ((parsed.protocol !== "https:" && !local) || parsed.origin !== candidate.replace(/\/$/, "")) {
    throw new Error("PUBLIC_SITE_URL must be a bare HTTPS origin.");
  }
  if (!configured && !local) throw new Error("PUBLIC_SITE_URL is not configured.");
  return parsed.origin;
}

export function getRuntimeEnv(): RuntimeEnv {
  return env as unknown as RuntimeEnv;
}

export function requireSecret(name: keyof RuntimeEnv): string {
  const value = getRuntimeEnv()[name];
  if (typeof value !== "string" || value.length < 8) throw new Error(`${String(name)} is not configured.`);
  return value;
}
