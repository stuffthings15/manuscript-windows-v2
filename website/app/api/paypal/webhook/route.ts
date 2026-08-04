import {
  recordSubscription,
  recordWebhookEvent,
  webhookEventSeen,
} from "../../../../lib/entitlements";
import {
  getSubscription,
  verifyPayPalWebhook,
} from "../../../../lib/paypal";
import {
  paypalWebhookEvent,
  subscriptionIdFromWebhook,
} from "../../../../lib/paypal-webhook";
import {
  readJsonBody,
  RequestBodyTooLargeError,
} from "../../../../lib/request-security";
import { requireSecret } from "../../../../lib/runtime";

export async function POST(request: Request) {
  let event;
  try {
    event = paypalWebhookEvent(await readJsonBody<unknown>(request, 65_536));
  } catch (error) {
    const status = error instanceof RequestBodyTooLargeError ? 413 : 400;
    return Response.json({ error: "Invalid webhook." }, { status });
  }

  try {
    if (!(await verifyPayPalWebhook(request.headers, event))) {
      return Response.json(
        { error: "Invalid webhook signature." },
        { status: 401 },
      );
    }
    if (await webhookEventSeen(event.id)) {
      return new Response(null, { status: 204 });
    }

    const subscriptionId = subscriptionIdFromWebhook(event);
    if (subscriptionId) {
      const subscription = await getSubscription(subscriptionId);
      if (subscription.plan_id !== requireSecret("PAYPAL_PLAN_ID")) {
        return Response.json(
          { error: "Unknown subscription plan." },
          { status: 403 },
        );
      }
      await recordSubscription(subscription);
    }

    await recordWebhookEvent(event.id, event.event_type);
    return new Response(null, { status: 204 });
  } catch {
    return Response.json(
      { error: "Webhook processing unavailable." },
      {
        status: 503,
        headers: { "Cache-Control": "no-store" },
      },
    );
  }
}
