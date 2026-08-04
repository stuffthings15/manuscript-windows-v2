const DEFAULT_PAYPAL_API = "https://api-m.paypal.com";
const DEFAULT_TIMEOUT_MS = 10_000;
const MAX_RESPONSE_BYTES = 128 * 1024;

type FetchLike = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

export interface PayPalSubscription {
  id: string;
  plan_id: string;
  custom_id?: string;
  status: string;
  subscriber?: { payer_id?: string; email_address?: string };
  billing_info?: { next_billing_time?: string };
}

export interface PayPalWebhookVerification {
  auth_algo: string;
  cert_url: string;
  transmission_id: string;
  transmission_sig: string;
  transmission_time: string;
  webhook_id: string;
  webhook_event: unknown;
}

interface PayPalClientOptions {
  clientId: string;
  clientSecret: string;
  fetchImpl?: FetchLike;
  now?: () => number;
  timeoutMs?: number;
  apiOrigin?: string;
}

export class PayPalTimeoutError extends Error {
  constructor() {
    super("PayPal request timed out.");
    this.name = "PayPalTimeoutError";
  }
}

export function createPayPalClient(options: PayPalClientOptions) {
  const fetchImpl = options.fetchImpl ?? fetch;
  const now = options.now ?? Date.now;
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const apiOrigin = new URL(options.apiOrigin ?? DEFAULT_PAYPAL_API).origin;
  if (!options.clientId || !options.clientSecret || !Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    throw new Error("PayPal client configuration is invalid.");
  }
  if (!apiOrigin.startsWith("https://")) {
    throw new Error("PayPal API origin must use HTTPS.");
  }

  let tokenCache: { value: string; refreshAt: number } | null = null;

  async function request(url: string, init?: RequestInit): Promise<Response> {
    const controller = new AbortController();
    let timeout: ReturnType<typeof setTimeout> | undefined;
    const timedOut = new Promise<never>((_resolve, reject) => {
      timeout = setTimeout(() => {
        const error = new PayPalTimeoutError();
        controller.abort(error);
        reject(error);
      }, timeoutMs);
    });
    try {
      return await Promise.race([
        fetchImpl(url, { ...init, signal: controller.signal }),
        timedOut,
      ]);
    } catch (error) {
      if (controller.signal.aborted) throw new PayPalTimeoutError();
      throw error;
    } finally {
      if (timeout) clearTimeout(timeout);
    }
  }

  async function accessToken(): Promise<string> {
    if (tokenCache && now() < tokenCache.refreshAt) return tokenCache.value;
    const credentials = btoa(`${options.clientId}:${options.clientSecret}`);
    const response = await request(`${apiOrigin}/v1/oauth2/token`, {
      method: "POST",
      headers: {
        Authorization: `Basic ${credentials}`,
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: "grant_type=client_credentials",
    });
    const payload = await responseJson<{
      access_token?: string;
      expires_in?: number;
    }>(response);
    if (!response.ok || !payload.access_token) {
      throw new Error("PayPal authentication failed.");
    }
    const expiresIn = Number.isFinite(payload.expires_in)
      ? Math.max(0, Number(payload.expires_in))
      : 300;
    tokenCache = {
      value: payload.access_token,
      refreshAt: now() + Math.max(0, expiresIn * 1_000 - 60_000),
    };
    return tokenCache.value;
  }

  return {
    async verifyWebhookSignature(payload: PayPalWebhookVerification): Promise<boolean> {
      const response = await request(
        `${apiOrigin}/v1/notifications/verify-webhook-signature`,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${await accessToken()}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify(payload),
        },
      );
      const result = await responseJson<{ verification_status?: string }>(response);
      return response.ok && result.verification_status === "SUCCESS";
    },

    async getSubscription(id: string): Promise<PayPalSubscription> {
      if (!/^I-[A-Z0-9]+$/.test(id)) {
        throw new Error("Invalid PayPal subscription.");
      }
      const response = await request(
        `${apiOrigin}/v1/billing/subscriptions/${encodeURIComponent(id)}`,
        { headers: { Authorization: `Bearer ${await accessToken()}` } },
      );
      const payload = await responseJson<PayPalSubscription>(response);
      if (
        !response.ok ||
        payload.id !== id ||
        typeof payload.plan_id !== "string" ||
        typeof payload.status !== "string"
      ) {
        throw new Error("PayPal subscription lookup failed.");
      }
      return payload;
    },
  };
}

async function responseJson<T>(response: Response): Promise<T> {
  const declared = response.headers.get("Content-Length");
  if (declared && Number(declared) > MAX_RESPONSE_BYTES) {
    throw new Error("PayPal response is too large.");
  }
  if (!response.body) throw new Error("PayPal response is empty.");

  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > MAX_RESPONSE_BYTES) {
        await reader.cancel();
        throw new Error("PayPal response is too large.");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as T;
}
