import { registerWithPassword } from "../../../../lib/account-identity";
import { sessionCookieHeaders } from "../../../../lib/account-session";
import {
  isSameOriginBrowserRequest,
  readFormDataBody,
  readJsonBody,
  RequestBodyTooLargeError,
} from "../../../../lib/request-security";
import { consumeAccountAuthAttempt } from "../../../../lib/entitlements";
import { identityProviderConfig, safeReturn } from "../../../account-auth";

interface RegisterBody {
  email?: unknown;
  password?: unknown;
  platform?: unknown;
  returnTo?: unknown;
}
export async function POST(request: Request) {
  try {
    const native = request.headers.get("Content-Type")?.startsWith("application/json");
    let body: RegisterBody;
    if (native) {
      body = await readJsonBody<RegisterBody>(request, 8_192);
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
    }    const result = await registerWithPassword(
      {
        email: body.email,
        password: body.password,
        platform: body.platform,
      },
      identityProviderConfig(),
    );
    if (result.status === "verification_required") {
      if (native) {
        return Response.json(
          { status: result.status, account: result.account },
          { status: 202, headers: { "Cache-Control": "no-store" } },
        );
      }
      return Response.redirect(
        new URL("/login?verify=1", request.url),
        303,
      );
    }
    if (native) {
      return Response.json(
        {
          status: "active",
          account: result.session.account,
          accessToken: result.session.accessToken,
          refreshToken: result.session.refreshToken,
          expiresIn: result.session.expiresIn,
          platform: result.session.platform,
        },
        { status: 201, headers: { "Cache-Control": "no-store" } },
      );
    }
    const headers = new Headers({
      Location: safeReturn(typeof body.returnTo === "string" ? body.returnTo : "/account"),
      "Cache-Control": "no-store",
    });
    for (const cookie of sessionCookieHeaders(result.session)) headers.append("Set-Cookie", cookie);
    return new Response(null, { status: 303, headers });
  } catch (error) {
    const status = error instanceof RequestBodyTooLargeError ? 413 : 400;
    const message = error instanceof Error ? error.message : "Account could not be created.";
    return Response.json(
      { error: status === 413 ? "Request is too large." : message },
      { status, headers: { "Cache-Control": "no-store" } },
    );
  }
}
