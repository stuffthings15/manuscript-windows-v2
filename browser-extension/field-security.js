(() => {
  "use strict";

  const SENSITIVE_AUTOCOMPLETE = new Set([
    "current-password",
    "new-password",
    "one-time-code",
    "cc-name",
    "cc-given-name",
    "cc-additional-name",
    "cc-family-name",
    "cc-number",
    "cc-exp",
    "cc-exp-month",
    "cc-exp-year",
    "cc-csc",
    "cc-type",
    "transaction-amount",
    "transaction-currency"
  ]);
  const SENSITIVE_LABEL = /\b(?:password|passphrase|passcode|one time code|otp|verification code|authentication code|auth code|security code|recovery code|backup code|credit card|card number|card cvc|card cvv|cvv|cvc|api key|api token|access token|access key|client secret|private key|secret key|seed phrase|wallet phrase)\b/;

  function normalize(value) {
    return String(value || "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, " ")
      .trim();
  }

  function isSensitiveFieldDescriptor(descriptor = {}) {
    if (normalize(descriptor.type) === "password") {
      return true;
    }
    const autocompleteTokens = String(descriptor.autocomplete || "")
      .toLowerCase()
      .split(/\s+/)
      .filter(Boolean);
    if (autocompleteTokens.some((token) => SENSITIVE_AUTOCOMPLETE.has(token))) {
      return true;
    }
    const label = normalize([
      descriptor.name,
      descriptor.id,
      descriptor.ariaLabel,
      descriptor.placeholder
    ].join(" "));
    return SENSITIVE_LABEL.test(label);
  }

  globalThis.ManuscriptFieldSecurity = Object.freeze({
    isSensitiveFieldDescriptor
  });
})();
