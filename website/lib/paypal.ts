import { requireSecret } from "./runtime";
import {
  createPayPalClient,
  PayPalSubscription,
  PayPalWebhookVerification,
} from "./paypal-client";
import {
  paypalWebhookVerification,
  PayPalWebhookEvent,
} from "./paypal-webhook";

export type { PayPalSubscription } from "./paypal-client";

let clientCache: {
  clientId: string;
  clientSecret: string;
  client: ReturnType<typeof createPayPalClient>;
} | null = null;

export async function getSubscription(id: string): Promise<PayPalSubscription> {
  const clientId = requireSecret("PAYPAL_CLIENT_ID");
  const clientSecret = requireSecret("PAYPAL_CLIENT_SECRET");
  if (
    !clientCache ||
    clientCache.clientId !== clientId ||
    clientCache.clientSecret !== clientSecret
  ) {
    clientCache = {
      clientId,
      clientSecret,
      client: createPayPalClient({ clientId, clientSecret }),
    };
  }
  return clientCache.client.getSubscription(id);
}

export async function verifyPayPalWebhook(
  headers: Headers,
  event: PayPalWebhookEvent,
): Promise<boolean> {
  const clientId = requireSecret("PAYPAL_CLIENT_ID");
  const clientSecret = requireSecret("PAYPAL_CLIENT_SECRET");
  if (
    !clientCache ||
    clientCache.clientId !== clientId ||
    clientCache.clientSecret !== clientSecret
  ) {
    clientCache = {
      clientId,
      clientSecret,
      client: createPayPalClient({ clientId, clientSecret }),
    };
  }
  const payload: PayPalWebhookVerification = paypalWebhookVerification(
    headers,
    event,
    requireSecret("PAYPAL_WEBHOOK_ID"),
  );
  return clientCache.client.verifyWebhookSignature(payload);
}
export function subscriptionAllowsAccess(status: string): boolean {
  return status === "ACTIVE";
}

export function subscriptionPeriodEnd(subscription: PayPalSubscription): number | null {
  const value = subscription.billing_info?.next_billing_time;
  return value ? Math.floor(Date.parse(value) / 1000) : null;
}
