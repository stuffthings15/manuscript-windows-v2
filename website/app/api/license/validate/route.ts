import {
  consumeLicenseValidationAttempt,
  validateLicense,
} from "../../../../lib/entitlements";
import {
  readJsonBody,
  RequestBodyTooLargeError,
} from "../../../../lib/request-security";

export async function POST(request: Request) {
  let body: { licenseKey?: string; deviceHash?: string };
  try {
    body = await readJsonBody(request, 4096);
  } catch (error) {
    const status = error instanceof RequestBodyTooLargeError ? 413 : 400;
    return Response.json({ active: false, reason: "invalid_request" }, { status });
  }
  if (typeof body.licenseKey !== "string" || typeof body.deviceHash !== "string") {
    return Response.json({ active: false, reason: "invalid_request" }, { status: 400 });
  }
  try {
    const address = request.headers.get("CF-Connecting-IP") || "unknown";
    if (!(await consumeLicenseValidationAttempt(address))) {
      return Response.json(
        { active: false, reason: "rate_limited" },
        { status: 429, headers: { "Cache-Control": "no-store", "Retry-After": "60" } },
      );
    }
    const result = await validateLicense(body.licenseKey, body.deviceHash);
    return Response.json(result, {
      status: result.active ? 200 : 403,
      headers: { "Cache-Control": "no-store" },
    });
  } catch {
    return Response.json({ active: false, reason: "service_unavailable" }, {
      status: 503,
      headers: { "Cache-Control": "no-store" },
    });
  }
}
