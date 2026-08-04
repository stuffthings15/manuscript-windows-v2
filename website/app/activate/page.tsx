import Link from "next/link";
import { issueLicenseFromSubscription } from "../../lib/entitlements";
import { requireAccountUser } from "../account-auth";

export const dynamic = "force-dynamic";

export default async function ActivatePage({ searchParams }: { searchParams: Promise<{ subscription_id?: string }> }) {
  const subscriptionId = (await searchParams).subscription_id;
  const user = await requireAccountUser(
    `/activate?subscription_id=${encodeURIComponent(subscriptionId ?? "")}`,
  );
  let result: { licenseKey: string; status: string } | null = null;
  let error = "";
  try {
    if (!subscriptionId) throw new Error("Missing PayPal subscription.");
    result = await issueLicenseFromSubscription(subscriptionId, user);
  } catch {
    error = "We could not confirm an active subscription. If payment just completed, wait a moment and refresh this page.";
  }
  return <main className="legal-page">
    <Link className="back" href="/">← Manuscript</Link>
    <p className="eyebrow">ACTIVATE MANUSCRIPT</p>
    <h1>{result ? "Your license is ready." : "Activation needs attention."}</h1>
    {result ? <>
      <p>Copy this license key into the Manuscript activation window. Treat it like a password.</p>
      <p>This key is also saved in the account for {user.email}.</p>
      <p className="license-key"><code>{result.licenseKey}</code></p>
      <form className="support-form" action="/api/portal" method="post">
        <input type="hidden" name="licenseKey" value={result.licenseKey} />
        <button className="button secondary" type="submit">Manage subscription</button>
      </form>
      <Link className="button secondary" href="/account">Open my account</Link>
    </> : <><p>{error}</p><a className="button secondary" href="/support">Contact support</a></>}
  </main>;
}
