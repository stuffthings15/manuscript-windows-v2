import { updatePasswordWithRecoveryToken } from "../../../../lib/account-identity";
import { consumeAccountAuthAttempt } from "../../../../lib/entitlements";
import {
  isSameOriginBrowserRequest,
  readJsonBody,
  RequestBodyTooLargeError,
} from "../../../../lib/request-security";
import { identityProviderConfig } from "../../../account-auth";

interface PasswordBody {
  accessToken?: unknown;
  password?: unknown;
}

export async function POST(request: Request) {
  if (!isSameOriginBrowserRequest(request)) {
    return Response.json(
      { error: "Invalid request origin." },
      { status: 403, headers: { "Cache-Control": "no-store" } },
    );
  }
  try {
    const body = await readJsonBody<PasswordBody>(request, 10_240);
    if (
      typeof body.accessToken !== "string" ||
      typeof body.password !== "string"
    ) {
      return Response.json(
        { error: "Recovery token and password are required." },
        { status: 400, headers: { "Cache-Control": "no-store" } },
      );
    }
    const clientAddress = request.headers.get("CF-Connecting-IP") || "unknown";
    if (!(await consumeAccountAuthAttempt("password-reset", clientAddress))) {
      return Response.json(
        { error: "Too many account attempts. Try again in one minute." },
        {
          status: 429,
          headers: { "Cache-Control": "no-store", "Retry-After": "60" },
        },
      );
    }
    await updatePasswordWithRecoveryToken(
      body.accessToken,
      body.password,
      identityProviderConfig(),
    );
    return Response.json(
      { ok: true },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch (error) {
    const status = error instanceof RequestBodyTooLargeError ? 413 : 400;
    return Response.json(
      {
        error: status === 413
          ? "Request is too large."
          : "The recovery link is invalid or expired.",
      },
      { status, headers: { "Cache-Control": "no-store" } },
    );
  }
}
