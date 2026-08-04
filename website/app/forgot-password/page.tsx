import Link from "next/link";

export default async function ForgotPasswordPage({
  searchParams,
}: {
  searchParams: Promise<{ sent?: string }>;
}) {
  const values = await searchParams;
  return <main className="legal-page">
    <Link className="back" href="/">← Manuscript</Link>
    <p className="eyebrow">ACCOUNT RECOVERY</p>
    <h1>Reset your password.</h1>
    {values.sent === "1"
      ? <p role="status">
          If that account exists, a secure recovery link was sent.
        </p>
      : <>
          <p>Enter the email used for your Manuscript account.</p>
          <form className="support-form" action="/api/auth/recover" method="post">
            <label>
              Email
              <input name="email" type="email" autoComplete="email" required />
            </label>
            <button className="button primary" type="submit">
              Send recovery link
            </button>
          </form>
        </>}
    <p><Link href="/login">Return to sign in</Link>.</p>
  </main>;
}
