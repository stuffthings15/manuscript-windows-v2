import {
  accountFromAccessToken,
  signInWithPassword,
} from "../../../../lib/account-identity";
import { processPendingAccountDeletionJobs } from "../../../../lib/account-deletion";
import {
  accessTokenFromRequest,
  clearSessionCookieHeaders,
} from "../../../../lib/account-session";
import {
  accountEntitlement,
  consumeAccountAuthAttempt,
  queueAccountDeletion,
} from "../../../../lib/entitlements";
import {
  isSameOriginBrowserRequest,
  readFormDataBody,
  RequestBodyTooLargeError,
} from "../../../../lib/request-security";
import { identityProviderConfig } from "../../../account-auth";

export async function POST(request: Request) {
  if (!isSameOriginBrowserRequest(request)) {
    return Response.json(
      { error: "Invalid request origin." },
      { status: 403, headers: { "Cache-Control": "no-store" } },
    );
  }
  let queued = false;
  try {
    const token = accessTokenFromRequest(request);
    if (!token) return redirectResult("/login?returnTo=%2Fdelete-account");
    const account = await accountFromAccessToken(token, identityProviderConfig());
    const form = await readFormDataBody(request, 4_096);
    const password = form.get("password");
    if (typeof password !== "string") {
      return redirectResult("/delete-account?status=failed");
    }
    const clientAddress = request.headers.get("CF-Connecting-IP") || "unknown";
    if (!(await consumeAccountAuthAttempt(account.email, clientAddress))) {
      return redirectResult("/delete-account?status=rate-limited");
    }
    const confirmed = await signInWithPassword(
      { email: account.email, password, platform: "web" },
      identityProviderConfig(),
    );
    if (confirmed.account.id !== account.id) {
      return redirectResult("/delete-account?status=failed");
    }
    const entitlement = await accountEntitlement(account.id);
    if (entitlement.active) {
      return redirectResult("/delete-account?status=cancel-first");
    }
    await queueAccountDeletion(account);
    queued = true;
    await processPendingAccountDeletionJobs(10);
    return redirectResult(
      "/delete-account?status=accepted",
      clearSessionCookieHeaders(),
    );
  } catch (error) {
    if (error instanceof RequestBodyTooLargeError) {
      return Response.json(
        { error: "Request is too large." },
        { status: 413, headers: { "Cache-Control": "no-store" } },
      );
    }
    return redirectResult(
      queued
        ? "/delete-account?status=accepted"
        : "/delete-account?status=failed",
      queued ? clearSessionCookieHeaders() : [],
    );
  }
}

function redirectResult(location: string, cookies: string[] = []): Response {
  const headers = new Headers({
    Location: location,
    "Cache-Control": "no-store",
  });
  for (const cookie of cookies) headers.append("Set-Cookie", cookie);
  return new Response(null, { status: 303, headers });
}
