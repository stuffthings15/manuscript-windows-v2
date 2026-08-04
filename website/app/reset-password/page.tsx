"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";

export default function ResetPasswordPage() {
  const [accessToken, setAccessToken] = useState("");
  const [status, setStatus] = useState("Reading recovery link.");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const parameters = new URLSearchParams(window.location.hash.slice(1));
    const token = parameters.get("access_token") ?? "";
    window.history.replaceState(null, "", window.location.pathname);
    if (token.length < 16) {
      setStatus("This recovery link is invalid or expired.");
      return;
    }
    setAccessToken(token);
    setReady(true);
    setStatus("Enter a new password.");
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const password = String(form.get("password") ?? "");
    const confirmation = String(form.get("confirmation") ?? "");
    if (password !== confirmation) {
      setStatus("Passwords do not match.");
      return;
    }
    setReady(false);
    setStatus("Changing password.");
    const response = await fetch("/api/auth/password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accessToken, password }),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => null) as {
        error?: string;
      } | null;
      setStatus(payload?.error ?? "Password could not be changed.");
      setReady(true);
      return;
    }
    setAccessToken("");
    setStatus("Password changed. You can sign in now.");
  }

  return <main className="legal-page">
    <Link className="back" href="/">← Manuscript</Link>
    <p className="eyebrow">ACCOUNT RECOVERY</p>
    <h1>Choose a new password.</h1>
    <p role="status">{status}</p>
    {ready
      ? <form className="support-form" onSubmit={submit}>
          <label>
            New password
            <input
              name="password"
              type="password"
              minLength={15}
              maxLength={128}
              autoComplete="new-password"
              required
            />
          </label>
          <label>
            Confirm new password
            <input
              name="confirmation"
              type="password"
              minLength={15}
              maxLength={128}
              autoComplete="new-password"
              required
            />
          </label>
          <button className="button primary" type="submit">
            Change password
          </button>
        </form>
      : null}
    <p><Link href="/login">Return to sign in</Link>.</p>
  </main>;
}
