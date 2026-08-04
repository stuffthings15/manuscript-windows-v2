import type { AccountIdentity, AccountPlatform } from "./account-identity";

const ACCOUNT_PLATFORMS = [
  "android",
  "windows",
  "macos",
  "ios",
  "linux",
  "web",
] as const;

const ACCESS_COOKIE = "ms_access";
const REFRESH_COOKIE = "ms_refresh";
const MAX_TOKEN_LENGTH = 8192;

interface SessionTokens {
  accessToken: string;
  refreshToken: string;
  expiresIn: number;
}
interface EntitlementState {
  active: boolean;
  status: string;
  checkedAt: string;
  nextCheckSeconds: number;
}

export function sessionCookieHeaders(session: SessionTokens): string[] {
  const accessAge = boundedAge(session.expiresIn, 60, 86_400);
  const refreshAge = 60 * 60 * 24 * 30;
  return [
    serializeCookie(ACCESS_COOKIE, session.accessToken, accessAge),
    serializeCookie(REFRESH_COOKIE, session.refreshToken, refreshAge),
  ];
}

export function clearSessionCookieHeaders(): string[] {
  return [
    serializeCookie(ACCESS_COOKIE, "", 0),
    serializeCookie(REFRESH_COOKIE, "", 0),
  ];
}

export function accessTokenFromRequest(request: Request): string | null {
  const authorization = request.headers.get("Authorization");
  if (authorization?.startsWith("Bearer ")) {
    return validToken(authorization.slice(7));
  }
  return validToken(cookieValue(request.headers.get("Cookie"), ACCESS_COOKIE));
}

export function refreshTokenFromRequest(request: Request): string | null {
  return validToken(cookieValue(request.headers.get("Cookie"), REFRESH_COOKIE));
}

export function platformFromRequest(request: Request): AccountPlatform {
  const value = request.headers.get("X-Manuscript-Platform") ?? "web";
  if ((ACCOUNT_PLATFORMS as readonly string[]).includes(value)) {
    return value as AccountPlatform;
  }
  throw new Error("Unsupported account platform.");
}

export function platformEntitlementResponse(
  account: AccountIdentity,
  entitlement: EntitlementState,
  platform: AccountPlatform,
) {
  return {
    account: { id: account.id, email: account.email },
    entitlement: {
      active: entitlement.active,
      status: entitlement.status,
      checkedAt: entitlement.checkedAt,
      nextCheckSeconds: entitlement.nextCheckSeconds,
    },
    platform,
  };
}

function serializeCookie(name: string, value: string, maxAge: number): string {
  return `${name}=${encodeURIComponent(value)}; Path=/; Max-Age=${maxAge}; HttpOnly; Secure; SameSite=Lax`;
}

function cookieValue(header: string | null, name: string): string | null {
  if (!header) return null;
  for (const part of header.split(";")) {
    const separator = part.indexOf("=");
    if (separator < 0 || part.slice(0, separator).trim() !== name) continue;
    try {
      return decodeURIComponent(part.slice(separator + 1).trim());
    } catch {
      return null;
    }
  }
  return null;
}

function validToken(value: string | null): string | null {
  if (!value || value.length < 16 || value.length > MAX_TOKEN_LENGTH) return null;
  if (Array.from(value).some((character) => {
    const code = character.charCodeAt(0);
    return /\s/u.test(character) || code < 32 || code === 127;
  })) return null;
  return value;
}

function boundedAge(value: number, minimum: number, maximum: number): number {
  if (!Number.isFinite(value)) return minimum;
  return Math.min(maximum, Math.max(minimum, Math.floor(value)));
}
