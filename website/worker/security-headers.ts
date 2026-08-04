const PAYPAL_ORIGINS =
  "https://*.paypal.com https://*.paypalobjects.com https://*.venmo.com";

export function securityHeaderEntries(path: string): Record<string, string> {
  const headers: Record<string, string> = {
    "Content-Security-Policy": [
      "default-src 'self'",
      "base-uri 'none'",
      `child-src 'self' ${PAYPAL_ORIGINS}`,
      `connect-src 'self' ${PAYPAL_ORIGINS}`,
      "font-src 'self' data:",
      "form-action 'self'",
      `frame-src 'self' ${PAYPAL_ORIGINS}`,
      "frame-ancestors 'none'",
      `img-src 'self' data: ${PAYPAL_ORIGINS}`,
      "object-src 'none'",
      `script-src 'self' ${PAYPAL_ORIGINS} 'unsafe-inline'`,
      `style-src 'self' ${PAYPAL_ORIGINS} 'unsafe-inline'`,
      "upgrade-insecure-requests",
    ].join("; "),
    "Cross-Origin-Opener-Policy": "same-origin-allow-popups",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains; preload",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
  };
  if (path === "/activate" || path.startsWith("/api/")) {
    headers["Cache-Control"] = "no-store";
  }
  return headers;
}
