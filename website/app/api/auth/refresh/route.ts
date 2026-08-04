import { refreshPasswordSession } from "../../../../lib/account-identity";
import {
  clearSessionCookieHeaders,
  refreshTokenFromRequest,
  sessionCookieHeaders,
} from "../../../../lib/account-session";
import { readJsonBody, RequestBodyTooLargeError } from "../../../../lib/request-security";
import { identityProviderConfig, safeReturn } from "../../../account-auth";

export async function GET(request: Request) {
  const refreshToken = refreshTokenFromRequest(request);
  const returnTo = safeReturn(new URL(request.url).searchParams.get("returnTo") ?? "/account");
  try {
    if (!refreshToken) throw new Error("Missing refresh token.");
    const session = await refreshPasswordSession(
      refreshToken,
      "web",
      identityProviderConfig(),
    );
    const headers = new Headers({
      Location: returnTo,
      "Cache-Control": "no-store",
    });
    for (const cookie of sessionCookieHeaders(session)) headers.append("Set-Cookie", cookie);
    return new Response(null, { status: 303, headers });
  } catch {
    const headers = new Headers({
      Location: `/login?returnTo=${encodeURIComponent(returnTo)}`,
      "Cache-Control": "no-store",
    });
    for (const cookie of clearSessionCookieHeaders()) headers.append("Set-Cookie", cookie);
    return new Response(null, { status: 303, headers });
  }
}


interface NativeRefreshBody {
  refreshToken?: unknown;
  platform?: unknown;
}

export async function POST(request: Request) {
  try {
    const body = await readJsonBody<NativeRefreshBody>(request, 9_216);
    if (
      typeof body.refreshToken !== "string" ||
      typeof body.platform !== "string" ||
      body.platform === "web"
    ) {
      return Response.json({ error: "Invalid refresh request." }, { status: 400 });
    }
    const session = await refreshPasswordSession(
      body.refreshToken,
      body.platform,
      identityProviderConfig(),
    );
    return Response.json(
      {
        account: session.account,
        accessToken: session.accessToken,
        refreshToken: session.refreshToken,
        expiresIn: session.expiresIn,
        platform: session.platform,
      },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch (error) {
    return Response.json(
      { error: "Account session could not be refreshed." },
      {
        status: error instanceof RequestBodyTooLargeError ? 413 : 401,
        headers: { "Cache-Control": "no-store" },
      },
    );
  }
}