import { signInWithPassword } from "../../../../lib/account-identity";
import { sessionCookieHeaders } from "../../../../lib/account-session";
import {
  isSameOriginBrowserRequest,
  readFormDataBody,
  readJsonBody,
  RequestBodyTooLargeError,
} from "../../../../lib/request-security";
import { consumeAccountAuthAttempt } from "../../../../lib/entitlements";
import { identityProviderConfig, safeReturn } from "../../../account-auth";

interface LoginBody {
  email?: unknown;
  password?: unknown;
  platform?: unknown;
  returnTo?: unknown;
}
export async function POST(request: Request) {
  try {
    const native = request.headers.get("Content-Type")?.startsWith("application/json");
    let body: LoginBody;
    if (native) {
      body = await readJsonBody<LoginBody>(request, 8_192);
    } else {
      if (!isSameOriginBrowserRequest(request)) {
        return Response.json({ error: "Invalid request origin." }, { status: 403 });
      }
      const form = await readFormDataBody(request, 8_192);
      body = {
        email: form.get("email"),
        password: form.get("password"),
        platform: "web",
        returnTo: form.get("returnTo"),
      };
    }
    if (
      typeof body.email !== "string" ||
      typeof body.password !== "string" ||
      typeof body.platform !== "string"
    ) {
      return Response.json({ error: "Email and password are required." }, { status: 400 });
    }
    const clientAddress = request.headers.get("CF-Connecting-IP") || "unknown";
    if (!(await consumeAccountAuthAttempt(body.email, clientAddress))) {
      return Response.json(
        { error: "Too many account attempts. Try again in one minute." },
        {
          status: 429,
          headers: { "Cache-Control": "no-store", "Retry-After": "60" },
        },
      );
    }    const session = await signInWithPassword(
      {
        email: body.email,
        password: body.password,
        platform: body.platform,
      },
      identityProviderConfig(),
    );
    if (native) {
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
    }
    const headers = new Headers({
      Location: safeReturn(typeof body.returnTo === "string" ? body.returnTo : "/account"),
      "Cache-Control": "no-store",
    });
    for (const cookie of sessionCookieHeaders(session)) headers.append("Set-Cookie", cookie);
    return new Response(null, { status: 303, headers });
  } catch (error) {
    const status = error instanceof RequestBodyTooLargeError ? 413 : 401;
    return Response.json(
      { error: status === 413 ? "Request is too large." : "Email or password is incorrect." },
      { status, headers: { "Cache-Control": "no-store" } },
    );
  }
}
