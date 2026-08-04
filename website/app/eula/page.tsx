import Link from "next/link";

export default function EulaPage() {
  return <main className="legal-page">
    <Link className="back" href="/">← Manuscript</Link>
    <p className="eyebrow">END USER LICENSE AGREEMENT</p><h1>A personal license, not a transfer of ownership.</h1>
    <p>Effective July 22, 2026. This agreement governs Manuscript software on supported platforms.</p>
    <h2>License grant</h2><p>While your subscription is active, you receive a limited, non-exclusive, non-transferable, revocable license to install and use Manuscript on the number of installations allowed by your plan for personal or internal business writing.</p>
    <h2>Restrictions</h2><p>You may not distribute the installer or license key, rent or sublicense the software, remove ownership notices, bypass entitlement controls, or modify or reverse engineer the software except where applicable law expressly permits.</p>
    <h2>Your content</h2><p>You retain ownership of your speech, drafts, documents, exports, corrections, and other content. You are responsible for having the right to process and use that content.</p>
    <h2>Local AI and third-party components</h2><p>Manuscript includes or interoperates with third-party software and local AI components that may have separate license notices. Those notices continue to apply to their components.</p>
    <h2>Updates and termination</h2><p>Updates may replace or change portions of the software. This license ends when your subscription entitlement ends or when this agreement is materially breached. After termination, stop using the app; locally created documents and lawful exports remain yours.</p>
    <h2>Security</h2><p>Do not tamper with code signing, license verification, local encryption, or safety controls. Report suspected vulnerabilities privately through the <a className="back" href="/support">support form</a>.</p>
  </main>;
}
