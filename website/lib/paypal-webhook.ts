import type { PayPalWebhookVerification } from "./paypal-client";

const EVENT_ID = /^[A-Za-z0-9._-]{1,128}$/;
const EVENT_TYPE = /^[A-Z0-9._-]{1,128}$/;
const SUBSCRIPTION_ID = /^I-[A-Z0-9]+$/;
const AUTH_ALGO = /^[A-Za-z0-9]{1,100}$/;

export interface PayPalWebhookEvent {
  id: string;
  event_type: string;
  resource?: {
    id?: string;
    billing_agreement_id?: string;
  };
}

export function paypalWebhookEvent(value: unknown): PayPalWebhookEvent {
  if (!value || typeof value !== "object") throw invalidMetadata();
  const candidate = value as Partial<PayPalWebhookEvent>;
  if (
    typeof candidate.id !== "string" ||
    !EVENT_ID.test(candidate.id) ||
    typeof candidate.event_type !== "string" ||
    !EVENT_TYPE.test(candidate.event_type) ||
    (candidate.resource !== undefined &&
      (!candidate.resource || typeof candidate.resource !== "object"))
  ) {
    throw invalidMetadata();
  }
  return candidate as PayPalWebhookEvent;
}

export function paypalWebhookVerification(
  headers: Headers,
  webhookEvent: PayPalWebhookEvent,
  webhookId: string,
): PayPalWebhookVerification {
  const authAlgo = headers.get("PAYPAL-AUTH-ALGO") ?? "";
  const certUrl = headers.get("PAYPAL-CERT-URL") ?? "";
  const transmissionId = headers.get("PAYPAL-TRANSMISSION-ID") ?? "";
  const transmissionSig = headers.get("PAYPAL-TRANSMISSION-SIG") ?? "";
  const transmissionTime = headers.get("PAYPAL-TRANSMISSION-TIME") ?? "";
  let cert: URL;
  try {
    cert = new URL(certUrl);
  } catch {
    throw invalidMetadata();
  }
  if (
    !AUTH_ALGO.test(authAlgo) ||
    !bounded(transmissionId, 100) ||
    !bounded(transmissionSig, 500) ||
    !bounded(transmissionTime, 100) ||
    !bounded(webhookId, 128) ||
    cert.protocol !== "https:" ||
    !(cert.hostname === "paypal.com" || cert.hostname.endsWith(".paypal.com")) ||
    cert.username ||
    cert.password ||
    certUrl.length > 500
  ) {
    throw invalidMetadata();
  }
  return {
    auth_algo: authAlgo,
    cert_url: cert.toString(),
    transmission_id: transmissionId,
    transmission_sig: transmissionSig,
    transmission_time: transmissionTime,
    webhook_id: webhookId,
    webhook_event: webhookEvent,
  };
}

export function subscriptionIdFromWebhook(
  event: PayPalWebhookEvent,
): string | null {
  const candidate = event.event_type.startsWith("BILLING.SUBSCRIPTION.")
    ? event.resource?.id
    : event.resource?.billing_agreement_id;
  return typeof candidate === "string" && SUBSCRIPTION_ID.test(candidate)
    ? candidate
    : null;
}

function bounded(value: string, max: number): boolean {
  return value.length >= 1 && value.length <= max;
}

function invalidMetadata(): Error {
  return new Error("PayPal webhook metadata is invalid.");
}
