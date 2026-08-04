import Link from "next/link";

import { accountSignInPath, getAccountUser } from "../account-auth";

export default async function DeleteAccountPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string }>;
}) {
  const [values, account] = await Promise.all([
    searchParams,
    getAccountUser(),
  ]);
  return <main className="legal-page">
    <Link className="back" href="/">← Manuscript</Link>
    <p className="eyebrow">ACCOUNT CONTROL</p>
    <h1>Delete your Manuscript account.</h1>
    {values.status === "accepted"
      ? <p role="status">
          Deletion was accepted. Hosted account data will be removed. Records
          required for payment disputes, taxes, fraud prevention, or another
          legal duty are retained without the Manuscript account link.
        </p>
      : null}
    {values.status === "cancel-first"
      ? <p role="alert">
          Cancel the active PayPal automatic payment from your account page,
          then return here.
        </p>
      : null}
    {values.status === "failed"
      ? <p role="alert">Deletion could not be verified. Try again.</p>
      : null}
    {values.status === "rate-limited"
      ? <p role="alert">Too many attempts. Wait one minute and try again.</p>
      : null}
    <h2>Before deletion</h2>
    <p>
      Account deletion does not cancel PayPal billing. Cancel the active PayPal
      automatic payment first. Local documents remain on your devices until you
      remove them.
    </p>
    {account && values.status !== "accepted"
      ? <form className="support-form" action="/api/account/delete" method="post">
          <p>Signed in as <strong>{account.email}</strong>.</p>
          <label>
            Confirm your password
            <input
              name="password"
              type="password"
              autoComplete="current-password"
              maxLength={128}
              required
            />
          </label>
          <button className="button primary" type="submit">
            Permanently delete account
          </button>
        </form>
      : null}
    {!account && values.status !== "accepted"
      ? <p>
          <Link className="button primary" href={accountSignInPath("/delete-account")}>
            Sign in to delete account
          </Link>
        </p>
      : null}
    <p>
      Cannot sign in? Use the <Link href="/support?subject=Delete%20account">
        deletion support form
      </Link>.
    </p>
  </main>;
}
