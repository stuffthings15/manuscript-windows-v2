import Link from "next/link";

export default async function SupportPage({ searchParams }: { searchParams: Promise<{ sent?: string; subject?: string }> }) {
  const values = await searchParams;
  const sent = values.sent === "1";
  const subject = values.subject?.slice(0, 120) ?? "";
  return <main className="legal-page">
    <Link className="back" href="/">← Manuscript</Link>
    <p className="eyebrow">SUPPORT</p><h1>Tell us what went wrong.</h1>
    <p>For billing, activation, privacy, or product help, send a short description below. Do not paste passwords, full payment-card numbers, private documents, or recordings.</p>
    {sent ? <p className="success-note">Your request was saved. Keep this page for your records.</p> : null}
    <form className="support-form" action="/api/support" method="post">
      <label>Email<input type="email" name="email" required maxLength={254} autoComplete="email" /></label>
      <label>Subject<input type="text" name="subject" required minLength={4} maxLength={120} defaultValue={subject} /></label>
      <label>Message<textarea name="message" required minLength={20} maxLength={4000} /></label>
      <label className="hidden-field" aria-hidden="true">Company website<input type="text" name="companyWebsite" tabIndex={-1} autoComplete="off" /></label>
      <button className="button primary" type="submit">Send support request</button>
    </form>
  </main>;
}
