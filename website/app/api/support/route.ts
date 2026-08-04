import { createSupportTicket } from "../../../lib/entitlements";
import {
  isSameOriginBrowserRequest,
  readFormDataBody,
  RequestBodyTooLargeError,
} from "../../../lib/request-security";

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export async function POST(request: Request) {
  if (!isSameOriginBrowserRequest(request)) {
    return Response.json({ error: "Request rejected." }, { status: 403 });
  }
  let form: FormData;
  try {
    form = await readFormDataBody(request, 8192);
  } catch (error) {
    const status = error instanceof RequestBodyTooLargeError ? 413 : 400;
    return Response.json({ error: "Request rejected." }, { status });
  }
  try {
    if (form.get("companyWebsite")) return Response.redirect(new URL("/support?sent=1", request.url), 303);
    const email = String(form.get("email") || "").trim();
    const subject = String(form.get("subject") || "").trim();
    const message = String(form.get("message") || "").trim();
    if (!EMAIL.test(email) || email.length > 254 || subject.length < 4 || subject.length > 120 || message.length < 20 || message.length > 4000) {
      return Response.json({ error: "Please check the support form." }, { status: 400 });
    }
    const address = request.headers.get("CF-Connecting-IP") || "unknown";
    await createSupportTicket(email, subject, message, address);
    return Response.redirect(new URL("/support?sent=1", request.url), 303);
  } catch {
    return Response.json({ error: "Support request could not be saved." }, { status: 503 });
  }
}
