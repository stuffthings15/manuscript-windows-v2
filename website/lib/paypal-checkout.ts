export interface PayPalSubscriptionRequest {
  plan_id: string;
  custom_id: string;
}

export function paypalSubscriptionRequest(
  planId: string,
  checkoutBinding: string,
): PayPalSubscriptionRequest {
  if (!planId.startsWith("P-") || !checkoutBinding.startsWith("MSCR1-")) {
    throw new Error("PayPal checkout is not configured safely.");
  }
  return {
    plan_id: planId,
    custom_id: checkoutBinding,
  };
}
