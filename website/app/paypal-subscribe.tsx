"use client";

import Script from "next/script";
import { useState } from "react";
import { paypalSubscriptionRequest } from "../lib/paypal-checkout";

declare global {
  interface Window {
    paypal?: {
      Buttons: (options: {
        createSubscription: (_data: unknown, actions: { subscription: { create: (value: { plan_id: string; custom_id: string }) => Promise<string> } }) => Promise<string>;
        onApprove: (data: { subscriptionID: string }) => void;
        onError: () => void;
      }) => { render: (target: string) => Promise<void> };
    };
  }
}

export function PayPalSubscribe({
  clientId,
  planId,
  checkoutBinding,
}: {
  clientId: string;
  planId: string;
  checkoutBinding: string;
}) {
  const [error, setError] = useState("");
  const render = () => {
    if (!window.paypal) return;
    window.paypal.Buttons({
      createSubscription: (_data, actions) =>
        actions.subscription.create(paypalSubscriptionRequest(planId, checkoutBinding)),
      onApprove: (data) => {
        window.location.assign(`/activate?subscription_id=${encodeURIComponent(data.subscriptionID)}`);
      },
      onError: () => setError("PayPal checkout could not open. Please try again."),
    }).render("#paypal-subscribe-button");
  };
  return <>
    <Script
      src={`https://www.paypal.com/sdk/js?client-id=${encodeURIComponent(clientId)}&vault=true&intent=subscription`}
      strategy="afterInteractive"
      onLoad={render}
    />
    <div id="paypal-subscribe-button" aria-label="Subscribe with PayPal" />
    {error ? <p className="card-note" role="alert">{error}</p> : null}
  </>;
}
