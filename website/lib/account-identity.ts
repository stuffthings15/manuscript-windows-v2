export const ACCOUNT_PLATFORMS = [
  "android",
  "windows",
  "macos",
  "ios",
  "linux",
  "web",
] as const;

export type AccountPlatform = (typeof ACCOUNT_PLATFORMS)[number];

export interface AccountIdentity {
  id: string;
  email: string;
  displayName: string;
}
export interface AccountSession {
  account: AccountIdentity;
  accessToken: string;
  refreshToken: string;
  expiresIn: number;
  platform: AccountPlatform;
}

export type RegistrationResult =
  | { status: "active"; session: AccountSession }
  | { status: "verification_required"; account: AccountIdentity };
export interface IdentityProviderConfig {
  origin: string;
  publicKey: string;
}

interface PasswordRequest {
  email: string;
  password: string;
  platform: string;
}

interface ProviderUser {
  id?: unknown;
  email?: unknown;
  user_metadata?: { full_name?: unknown; name?: unknown };
}

interface ProviderSessionResponse {
  access_token?: unknown;
  refresh_token?: unknown;
  expires_in?: unknown;
  user?: ProviderUser;
}

type Fetcher = (
  input: string | URL | Request,
  init?: RequestInit,
) => Promise<Response>;

const ACCOUNT_ID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export async function signInWithPassword(
  request: PasswordRequest,
  config: IdentityProviderConfig,
  fetcher: Fetcher = fetch,
): Promise<AccountSession> {
  const platform = requirePlatform(request.platform);
  const response = await fetcher(
    `${providerOrigin(config)}/auth/v1/token?grant_type=password`,
    {
      method: "POST",
      headers: providerHeaders(config),
      body: JSON.stringify({
        email: normalizeEmail(request.email),
        password: request.password,
      }),
    },
  );
  if (!response.ok) throw new Error("Email or password is incorrect.");
  return sessionFromProvider(await response.json(), platform);
}

export async function registerWithPassword(
  request: PasswordRequest,
  config: IdentityProviderConfig,
  fetcher: Fetcher = fetch,
): Promise<RegistrationResult> {
  if (request.password.length < 15) {
    throw new Error("Password must be at least 15 characters.");
  }
  if (request.password.length > 128) {
    throw new Error("Password must be no more than 128 characters.");
  }
  const platform = requirePlatform(request.platform);
  const response = await fetcher(`${providerOrigin(config)}/auth/v1/signup`, {
    method: "POST",
    headers: providerHeaders(config),
    body: JSON.stringify({
      email: normalizeEmail(request.email),
      password: request.password,
    }),
  });
  if (!response.ok) throw new Error("Account could not be created.");
  const payload = await response.json() as ProviderSessionResponse;
  if (
    typeof payload.access_token === "string" &&
    typeof payload.refresh_token === "string"
  ) {
    return { status: "active", session: sessionFromProvider(payload, platform) };
  }
  if (!payload.user) {
    throw new Error("Identity provider returned an invalid account.");
  }
  return {
    status: "verification_required",
    account: identityFromProvider(payload.user),
  };
}

export async function refreshPasswordSession(
  refreshToken: string,
  platformValue: string,
  config: IdentityProviderConfig,
  fetcher: Fetcher = fetch,
): Promise<AccountSession> {
  const platform = requirePlatform(platformValue);
  if (refreshToken.length < 16 || refreshToken.length > 8192) {
    throw new Error("Account session is invalid.");
  }
  const response = await fetcher(
    `${providerOrigin(config)}/auth/v1/token?grant_type=refresh_token`,
    {
      method: "POST",
      headers: providerHeaders(config),
      body: JSON.stringify({ refresh_token: refreshToken }),
    },
  );
  if (!response.ok) throw new Error("Account session is invalid.");
  return sessionFromProvider(await response.json(), platform);
}
export async function requestPasswordRecovery(
  email: string,
  returnUrl: string,
  config: IdentityProviderConfig,
  fetcher: Fetcher = fetch,
): Promise<void> {
  const parsedReturn = new URL(returnUrl);
  if (
    parsedReturn.protocol !== "https:" ||
    parsedReturn.username ||
    parsedReturn.password ||
    parsedReturn.hash ||
    parsedReturn.origin === "null"
  ) {
    throw new Error("Password recovery return URL must use HTTPS.");
  }
  const response = await fetcher(
    `${providerOrigin(config)}/auth/v1/recover?redirect_to=${encodeURIComponent(parsedReturn.href)}`,
    {
      method: "POST",
      headers: providerHeaders(config),
      body: JSON.stringify({ email: normalizeEmail(email) }),
    },
  );
  if (!response.ok) throw new Error("Password recovery could not be started.");
}

export async function updatePasswordWithRecoveryToken(
  accessToken: string,
  password: string,
  config: IdentityProviderConfig,
  fetcher: Fetcher = fetch,
): Promise<AccountIdentity> {
  if (accessToken.length < 16 || accessToken.length > 8192 || /\s/u.test(accessToken)) {
    throw new Error("Password recovery session is invalid.");
  }
  if (password.length < 15) {
    throw new Error("Password must be at least 15 characters.");
  }
  if (password.length > 128) {
    throw new Error("Password must be no more than 128 characters.");
  }
  const response = await fetcher(`${providerOrigin(config)}/auth/v1/user`, {
    method: "PUT",
    headers: {
      ...providerHeaders(config),
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify({ password }),
  });
  if (!response.ok) throw new Error("Password could not be changed.");
  return identityFromProvider(await response.json());
}
export async function deleteIdentityAccount(
  accountIdValue: string,
  adminKey: string,
  config: IdentityProviderConfig,
  fetcher: Fetcher = fetch,
): Promise<void> {
  const accountId = accountIdValue.trim().toLowerCase();
  if (!ACCOUNT_ID_PATTERN.test(accountId)) {
    throw new Error("Account identity is invalid.");
  }
  if (adminKey.length < 24 || adminKey.length > 8192 || /\s/u.test(adminKey)) {
    throw new Error("Identity provider administrator key is not configured.");
  }
  const response = await fetcher(
    `${providerOrigin(config)}/auth/v1/admin/users/${accountId}`,
    {
      method: "DELETE",
      headers: {
        ...providerHeaders(config),
        apikey: adminKey,
        Authorization: `Bearer ${adminKey}`,
      },
    },
  );
  if (!response.ok && response.status !== 404) {
    throw new Error("Identity account could not be deleted.");
  }
}
export async function accountFromAccessToken(
  accessToken: string,
  config: IdentityProviderConfig,
  fetcher: Fetcher = fetch,
): Promise<AccountIdentity> {
  if (accessToken.length < 16 || accessToken.length > 8192) {
    throw new Error("Account session is invalid.");
  }
  const response = await fetcher(`${providerOrigin(config)}/auth/v1/user`, {
    method: "GET",
    headers: {
      ...providerHeaders(config),
      Authorization: `Bearer ${accessToken}`,
    },
  });
  if (!response.ok) throw new Error("Account session is invalid.");
  return identityFromProvider(await response.json());
}

function sessionFromProvider(
  value: unknown,
  platform: AccountPlatform,
): AccountSession {
  const session = value as ProviderSessionResponse;
  if (
    typeof session.access_token !== "string" ||
    typeof session.refresh_token !== "string" ||
    typeof session.expires_in !== "number" ||
    !Number.isFinite(session.expires_in) ||
    session.expires_in < 60 ||
    !session.user
  ) {
    throw new Error("Identity provider returned an invalid session.");
  }
  return {
    account: identityFromProvider(session.user),
    accessToken: session.access_token,
    refreshToken: session.refresh_token,
    expiresIn: Math.floor(session.expires_in),
    platform,
  };
}

function identityFromProvider(user: ProviderUser): AccountIdentity {
  if (
    typeof user.id !== "string" ||
    !ACCOUNT_ID_PATTERN.test(user.id) ||
    typeof user.email !== "string"
  ) {
    throw new Error("Identity provider returned an invalid account.");
  }
  const email = normalizeEmail(user.email);
  const proposedName = user.user_metadata?.full_name ?? user.user_metadata?.name;
  const displayName =
    typeof proposedName === "string" && proposedName.trim().length > 0
      ? proposedName.trim().slice(0, 120)
      : email;
  return { id: user.id.toLowerCase(), email, displayName };
}

function providerHeaders(
  config: IdentityProviderConfig,
): Record<string, string> {
  if (config.publicKey.length < 8 || config.publicKey.length > 4096) {
    throw new Error("Identity provider public key is not configured.");
  }
  return {
    apikey: config.publicKey,
    "Content-Type": "application/json",
  };
}

function providerOrigin(config: IdentityProviderConfig): string {
  const parsed = new URL(config.origin);
  const local =
    parsed.hostname === "localhost" || parsed.hostname === "127.0.0.1";
  if ((!local && parsed.protocol !== "https:") || parsed.origin !== config.origin) {
    throw new Error("Identity provider origin must be a bare HTTPS origin.");
  }
  return parsed.origin;
}

function requirePlatform(value: string): AccountPlatform {
  if ((ACCOUNT_PLATFORMS as readonly string[]).includes(value)) {
    return value as AccountPlatform;
  }
  throw new Error("Unsupported account platform.");
}

function normalizeEmail(value: string): string {
  const email = value.trim().toLowerCase();
  if (
    email.length > 254 ||
    !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)
  ) {
    throw new Error("A valid account email is required.");
  }
  return email;
}
