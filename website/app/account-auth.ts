import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import {
  accountFromAccessToken,
  type AccountIdentity,
  type IdentityProviderConfig,
} from "../lib/account-identity";
import { safeAccountReturn } from "../lib/account-redirect";
import { getRuntimeEnv } from "../lib/runtime";

export function identityProviderConfig(): IdentityProviderConfig {
  const runtime = getRuntimeEnv();
  const origin = runtime.IDENTITY_PROVIDER_URL?.trim() ?? "";
  const publicKey = runtime.IDENTITY_PROVIDER_PUBLIC_KEY?.trim() ?? "";
  if (!origin || !publicKey) {
    throw new Error("Account service is not configured.");
  }
  return { origin, publicKey };
}

export async function getAccountUser(): Promise<AccountIdentity | null> {
  const cookieStore = await cookies();
  const accessToken = cookieStore.get("ms_access")?.value;
  if (!accessToken) return null;
  try {
    return await accountFromAccessToken(accessToken, identityProviderConfig());
  } catch {
    return null;
  }
}

export async function requireAccountUser(
  returnTo: string,
): Promise<AccountIdentity> {
  const user = await getAccountUser();
  if (user) return user;
  const cookieStore = await cookies();
  const safe = safeAccountReturn(returnTo);
  if (cookieStore.get("ms_refresh")?.value) {
    redirect(`/api/auth/refresh?returnTo=${encodeURIComponent(safe)}`);
  }
  redirect(accountSignInPath(safe));
}

export function accountSignInPath(returnTo = "/"): string {
  return `/login?returnTo=${encodeURIComponent(safeAccountReturn(returnTo))}`;
}

export { safeAccountReturn as safeReturn };
