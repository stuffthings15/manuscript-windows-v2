import Link from "next/link";

export default async function SignupPage({
  searchParams,
}: {
  searchParams: Promise<{ returnTo?: string }>;
}) {
  const values = await searchParams;
  return <main className="legal-page">
    <Link className="back" href="/">← Manuscript</Link>
    <p className="eyebrow">MANUSCRIPT ACCOUNT</p>
    <h1>Create one account for every platform.</h1>
    <p>Use this account on Windows, Android, and future Manuscript apps.</p>
    <form className="support-form" action="/api/auth/register" method="post">
      <input type="hidden" name="returnTo" value={values.returnTo ?? "/account"} />
      <label>Email<input name="email" type="email" autoComplete="email" required /></label>
      <label>Password<input name="password" type="password" minLength={15} maxLength={128} autoComplete="new-password" required /></label>
      <p className="card-note">Use at least 15 characters. A long passphrase works well.</p>
      <button className="button primary" type="submit">Create account</button>
    </form>
    <p>Already have an account? <Link href={`/login?returnTo=${encodeURIComponent(values.returnTo ?? "/account")}`}>Sign in</Link>.</p>
  </main>;
}
