import Link from "next/link";

export default function PrivacyPage() {
  return <main className="legal-page">
    <Link className="back" href="/">← Manuscript</Link>
    <p className="eyebrow">PRIVACY POLICY</p><h1>Private writing. Limited billing data.</h1>
    <p>Effective July 22, 2026. This policy explains how Manuscript apps and the subscription website handle information.</p>
    <h2>Information the app handles</h2>
    <p>Speech audio, transcripts, documents, corrections, local AI requests, and writing history are processed on your device. Optional history, recovery, learning, and speech-improvement records are stored locally under your control. Manuscript does not send that product content to the subscription service.</p>
    <h2>Subscription information</h2>
    <p>PayPal processes checkout, payment, tax, and billing information under its own privacy terms. The identity provider receives your email and password for account authentication. Manuscript does not store the password. Manuscript receives the stable account ID and email, PayPal payer and subscription identifiers, subscription status, and renewal information. We do not receive full payment-card numbers.</p>
    <h2>License and installation information</h2>
    <p>The entitlement service stores a one-way hash of your license key, a pseudonymous installation hash, activation timestamps, and subscription status. These records are used only to issue licenses, enforce installation limits, prevent abuse, and confirm paid access.</p>
    <h2>Support information</h2>
    <p>If you submit a support request, we store the email address, subject, message, and submission time needed to answer it. Do not include private document text, passwords, payment-card details, or sensitive recordings.</p>
    <h2>Sharing and retention</h2>
    <p>Information is shared with service providers only as needed to operate billing, hosting, security, and support. Subscription and license records are retained while the account is active and afterward as needed for accounting, fraud prevention, disputes, and legal obligations. Support records are retained only as long as needed to resolve and document the request.</p>
    <h2>Your choices</h2>
    <p>You can manage or cancel billing through PayPal automatic payments. You can remove local app data using Manuscript&apos;s privacy controls. For access, correction, or privacy questions about hosted records, use the <a className="back" href="/support">support form</a>. To remove an account, use the <a className="back" href="/delete-account">account deletion page</a>. Active PayPal billing must be cancelled first. Deletion removes the identity account and support text, then removes the account link from payment records that must be retained.</p>
    <h2>Security and changes</h2>
    <p>We use transport encryption, verified PayPal subscription checks, hashed license records, encrypted local storage, and restricted service access. No system is perfectly secure. Material policy changes will be posted here with a new effective date.</p>
  </main>;
}
