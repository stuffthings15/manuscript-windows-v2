import { requestPasswordRecovery } from "../../../../lib/account-identity";
import { consumeAccountAuthAttempt } from "../../../../lib/entitlements";
import { publicSiteOrigin } from "../../../../lib/runtime";
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
  try {
    const form = await readFormDataBody(request, 4_096);
    const email = form.get("email");
    if (typeof email !== "string") {
      return Response.json(
        { error: "Email is required." },
        { status: 400, headers: { "Cache-Control": "no-store" } },
      );
    }
    const clientAddress = request.headers.get("CF-Connecting-IP") || "unknown";
    if (!(await consumeAccountAuthAttempt(email, clientAddress))) {
      return Response.json(
        { error: "Too many account attempts. Try again in one minute." },
        {
          status: 429,
          headers: { "Cache-Control": "no-store", "Retry-After": "60" },
        },
      );
    }
    await requestPasswordRecovery(
      email,
      `${publicSiteOrigin(request.url)}/reset-password`,
      identityProviderConfig(),
    );
  } catch (error) {
    if (error instanceof RequestBodyTooLargeError) {
      return Response.json(
        { error: "Request is too large." },
        { status: 413, headers: { "Cache-Control": "no-store" } },
      );
    }
    // The same response hides whether an account exists.
  }
  return new Response(null, {
    status: 303,
    headers: {
      Location: "/forgot-password?sent=1",
      "Cache-Control": "no-store",
    },
  });
}
