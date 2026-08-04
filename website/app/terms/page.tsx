import Link from "next/link";

export default function TermsPage() {
  return <main className="legal-page">
    <Link className="back" href="/">← Manuscript</Link>
    <p className="eyebrow">SUBSCRIPTION TERMS</p><h1>Clear terms for continued access.</h1>
    <p>Effective July 22, 2026. By purchasing or using Manuscript, you agree to these terms and the End User License Agreement.</p>
    <h2>Subscription and renewal</h2>
    <p>The checkout page shows the price, currency, billing interval, applicable taxes, and renewal terms before purchase. Your subscription renews automatically until canceled. You authorize PayPal to charge the selected payment method for each renewal.</p>
    <h2>Cancellation and access</h2>
    <p>You may cancel through PayPal automatic payments. Cancellation stops future renewals and normally takes effect at the end of the paid billing period shown in PayPal. Product access requires an active entitlement and periodic internet verification. Access stops when the entitlement service reports that the subscription is inactive.</p>
    <h2>Trials, promotions, and refunds</h2>
    <p>Any trial or promotion is governed by the terms shown at checkout. Refunds are provided where required by applicable law and otherwise under the policy displayed at purchase. Canceling does not automatically refund charges already paid.</p>
    <h2>Acceptable use</h2>
    <p>You may not share or resell license keys, bypass subscription checks or installation limits, reverse engineer the service except where law permits, disrupt the service, or use it unlawfully.</p>
    <h2>Availability and changes</h2>
    <p>We may update the app, security controls, supported platforms, and features. We may suspend access for fraud, abuse, chargebacks, security threats, or violations of these terms. Planned material subscription changes will be disclosed as required by law.</p>
    <h2>Disclaimers and liability</h2>
    <p>Manuscript is a writing aid and may make errors. You are responsible for reviewing output before use. To the maximum extent permitted by law, the service is provided without implied warranties and liability is limited to the amount you paid for the service during the twelve months before the claim. Rights that cannot legally be excluded remain unaffected.</p>
    <h2>Questions</h2><p>Use the <a className="back" href="/support">support form</a> for billing, legal, or subscription questions.</p>
  </main>;
}
