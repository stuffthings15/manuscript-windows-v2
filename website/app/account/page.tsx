import Link from "next/link";
import { accountLicense } from "../../lib/entitlements";
import { requireAccountUser } from "../account-auth";

export const dynamic = "force-dynamic";

export default async function AccountPage() {
  const user = await requireAccountUser("/account");
  const account = await accountLicense(user);
  return <main className="legal-page">
    <Link className="back" href="/">← Manuscript</Link>
    <p className="eyebrow">YOUR MANUSCRIPT ACCOUNT</p>
    <h1>{user.displayName}</h1>
    <p>{user.email}</p>
    {account ? <>
      <h2>Subscription</h2>
      <p>Status: <strong>{account.status}</strong></p>
      {account.active ? <>
        <p>Your production license key:</p>
        <p className="license-key"><code>{account.licenseKey}</code></p>
        <p>Keep this key private. Manuscript checks that your subscription remains active.</p>
      </> : <p>Your license is paused because the PayPal subscription is not active.</p>}
    </> : <>
      <h2>No subscription linked yet</h2>
      <p>Subscribe with PayPal, then return here using the same signed-in account.</p>
      <Link className="button primary" href="/#pricing">View subscription</Link>
    </>}
    <p><Link className="back" href="/delete-account">Delete account</Link></p>
    <form action="/api/auth/logout" method="post"><button className="button secondary" type="submit">Sign out</button></form>
  </main>;
}
