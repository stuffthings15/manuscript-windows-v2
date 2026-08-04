import Link from "next/link";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ returnTo?: string; verify?: string }>;
}) {
  const values = await searchParams;
  return <main className="legal-page">
    <Link className="back" href="/">← Manuscript</Link>
    <p className="eyebrow">MANUSCRIPT ACCOUNT</p>
    <h1>Sign in.</h1>
    {values.verify === "1"
      ? <p role="status">Check your email, verify your account, then sign in.</p>
      : null}
    <form className="support-form" action="/api/auth/login" method="post">
      <input type="hidden" name="returnTo" value={values.returnTo ?? "/account"} />
      <label>Email<input name="email" type="email" autoComplete="email" required /></label>
      <label>Password<input name="password" type="password" autoComplete="current-password" required /></label>
      <button className="button primary" type="submit">Sign in</button>
    </form>
    <p><Link href="/forgot-password">Forgot password?</Link></p>
    <p>New to Manuscript? <Link href={`/signup?returnTo=${encodeURIComponent(values.returnTo ?? "/account")}`}>Create an account</Link>.</p>
  </main>;
}
