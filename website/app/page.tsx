import Image from "next/image";
import { PayPalSubscribe } from "./paypal-subscribe";
import { getRuntimeEnv } from "../lib/runtime";
import { checkoutBindingForAccount } from "../lib/crypto";
import { accountSignInPath, getAccountUser } from "./account-auth";

const features = [
  ["Private by architecture", "There is no code path that uploads your writing. AI runs on your device, requests are locked to your own machine, and local storage is encrypted."],
  ["Works where the internet does not", "Planes, secure buildings, field work, dead spots. Dictation and correction run entirely offline, at full quality."],
  ["You approve every change", "Manuscript shows each correction with the original beside it and touches your document only when you accept. Names, numbers, dates, and links are protected."],
  ["One account, desk and pocket", "A single subscription covers the Windows app and the Android app. Both correct on-device; the account carries your identity, never your words."],
];

export default async function Home() {
  const runtime = getRuntimeEnv();
  const clientId = runtime.PAYPAL_CLIENT_ID?.trim() ?? "";
  const planId = runtime.PAYPAL_PLAN_ID?.trim() ?? "";
  const bindingSecret = runtime.LICENSE_DERIVATION_SECRET?.trim() ?? "";
  const identityReady = (runtime.IDENTITY_PROVIDER_URL?.length ?? 0) >= 8 &&
    (runtime.IDENTITY_PROVIDER_PUBLIC_KEY?.length ?? 0) >= 8;
  const checkoutReady = clientId.length >= 8 && planId.length >= 8 &&
    bindingSecret.length >= 8 && identityReady;
  const user = checkoutReady ? await getAccountUser() : null;
  const checkoutBinding = user
    ? await checkoutBindingForAccount(user.id, bindingSecret)
    : "";
  return (
    <main>
      <header className="site-header">
        <a className="brand" href="#top" aria-label="Manuscript home">
          <Image src="/manuscript-icon.png" alt="" width={36} height={36} />
          <span>Manuscript</span>
        </a>
        <nav aria-label="Main navigation">
          <a href="#features">Features</a>
          <a href="#privacy">Privacy</a>
          <a href="#pricing">Subscribe</a>
          <a href="/account">Account</a>
          <a href="/support">Support</a>
        </nav>
      </header>

      <section className="hero" id="top">
        <div className="hero-copy">
          <p className="eyebrow">PRIVATE, ON-DEVICE WRITING AI</p>
          <h1>Flawless writing powered locally.<br /><em>Your words never leave your device.</em></h1>
          <p className="hero-lede">
            Manuscript corrects grammar, polishes style, and turns speech into finished text with
            AI that runs entirely on your own computer and phone. No cloud. Nothing uploaded,
            logged, or used to train anything.
          </p>
          <div className="hero-actions">
            <a className="button primary" href="#pricing">Start subscription</a>
            <a className="button secondary" href="#how-it-works">See how it works</a>
          </div>
          <p className="microcopy">Secure checkout and billing are handled by PayPal. Cancel through your PayPal account.</p>
        </div>
        <div className="product-frame" aria-label="Manuscript brand artwork">
          <div className="frame-bar"><span /><span /><span /><b>Manuscript</b></div>
          <Image
            src="/manuscript-art-v3.png"
            alt="Manuscript artwork with the words Flawless writing powered locally"
            width={1730}
            height={909}
            priority
            sizes="(max-width: 980px) calc(100vw - 40px), 50vw"
          />
        </div>
      </section>

      <section className="proof-strip" aria-label="Product principles">
        <span>On-device AI</span><span>Full function offline</span><span>Encrypted local storage</span><span>One account, Windows + Android</span>
      </section>

      <section className="section" id="features">
        <div className="section-heading">
          <p className="eyebrow">BUILT FOR THE SENTENCE IN FRONT OF YOU</p>
          <h2>One quiet tool for speaking, rewriting, and finishing.</h2>
        </div>
        <div className="feature-grid">
          {features.map(([title, description], index) => (
            <article className="feature-card" key={title}>
              <span className="feature-number">0{index + 1}</span>
              <h3>{title}</h3>
              <p>{description}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="section split" id="how-it-works">
        <div>
          <p className="eyebrow">A FASTER PATH FROM THOUGHT TO PAGE</p>
          <h2>Speak. Shape. Send.</h2>
          <p className="section-copy">Hold your chosen shortcut, speak naturally, and release. Manuscript transcribes locally, cleans the result, and inserts it back into the app you were using.</p>
        </div>
        <ol className="steps">
          <li><strong>Capture</strong><span>Push-to-talk or hands-free dictation with a compact, focus-safe status bar.</span></li>
          <li><strong>Refine</strong><span>Review, proofread, rewrite, shorten, expand, summarize, or translate selected text.</span></li>
          <li><strong>Approve</strong><span>Keep the final say before text is inserted, copied, or saved.</span></li>
        </ol>
      </section>

      <section className="privacy-panel" id="privacy">
        <div>
          <p className="eyebrow">PRIVATE BY ARCHITECTURE, NOT BY PROMISE.</p>
          <h2>Your words are not the product.</h2>
        </div>
        <div>
          <p>Cloud writing tools must send your text to their servers. Manuscript cannot: its AI requests are restricted to your own machine by code, its local database is encrypted, and it collects no analytics and no content logs. The subscription service receives only billing data handled by PayPal, subscription status, your account ID and email, a subscription identifier, and a pseudonymous installation identifier - never your writing, audio, or documents.</p>
          <a href="/privacy">Read the privacy policy →</a>
        </div>
      </section>

      <section className="section pricing-section" id="pricing">
        <div className="pricing-copy">
          <p className="eyebrow">ONE ACTIVE SUBSCRIPTION</p>
          <h2>Use the complete app while your plan is active.</h2>
          <p>The checkout page shows the final recurring price, billing interval, taxes, and renewal terms before you pay.</p>
        </div>
        <div className="price-card">
          <p className="plan-name">Manuscript</p>
          <h3>$5 <small>/ month</small></h3>
          <ul>
            <li>Local dictation and writing suite</li>
            <li>App updates during the subscription</li>
            <li>Activation on up to two supported installations</li>
            <li>Self-service billing and cancellation</li>
          </ul>
          {checkoutReady && checkoutBinding
            ? <PayPalSubscribe clientId={clientId} planId={planId} checkoutBinding={checkoutBinding} />
            : checkoutReady
              ? <a className="button primary" href={accountSignInPath("/#pricing")}>Sign in to subscribe</a>
            : <p className="checkout-pending" role="status">Checkout setup is pending. The private test page is available, but payments are not open yet.</p>}
          <p className="card-note">Access requires an active subscription and a brief online entitlement check.</p>
          <p className="card-note">One account unlocks supported Windows and Android apps. Future platforms will use the same account.</p>
        </div>
      </section>

      <section className="section faq">
        <div className="section-heading"><p className="eyebrow">GOOD TO KNOW</p><h2>Before you subscribe.</h2></div>
        <div className="faq-list">
          <details><summary>Does Manuscript upload my audio or writing?</summary><p>No. Product speech, documents, and writing AI requests stay on your computer. Subscription verification sends only license and installation identifiers.</p></details>
          <details><summary>What happens if my subscription ends?</summary><p>The app stops at the activation screen after the entitlement service reports that the subscription is no longer active. Your locally stored documents and exports remain yours.</p></details>
          <details><summary>Can I cancel online?</summary><p>Yes. PayPal automatic payments lets you manage payment details and cancel without contacting support.</p></details>
          <details><summary>Does it work without the internet?</summary><p>Writing features run locally, but the paid app requires periodic online subscription verification.</p></details>
        </div>
      </section>

      <footer>
        <div className="brand"><Image src="/manuscript-icon.png" alt="" width={30} height={30} /><span>Manuscript</span></div>
        <p>Flawless writing powered locally</p>
        <div><a href="/privacy">Privacy</a><a href="/terms">Terms</a><a href="/eula">EULA</a><a href="/support">Support</a></div>
      </footer>
    </main>
  );
}
